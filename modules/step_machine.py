"""
step_machine.py - 핵심 STEP 0~8 대화 로직

STEP 0: 메뉴 선택 (본인확인)
STEP 1: 캠페인 선택 (카드 UI)
STEP 2: 몇 개 계정 진행? (버튼)
STEP 3: 아이디 수집 (기존 아이디 버튼 + 신규 입력)
STEP 4: 옵션 선택 + 구매 가이드 전달 + 양식 요청
STEP 5: 양식 접수 (수취인명, 연락처, 은행, 계좌, 예금주, 주소)
STEP 6: 구매캡쳐 대기
STEP 7: 리뷰캡쳐 대기
STEP 8: 완료 (입금대기)

모든 STEP에 뒤로가기/취소 버튼 포함.
"""

import re
import json
import logging
from modules.state_store import StateStore, ReviewerState
from modules.form_parser import parse_menu_choice, parse_campaign_choice, parse_full_form, parse_multiple_forms
from modules.campaign_manager import CampaignManager
from modules.reviewer_manager import ReviewerManager
from modules.chat_logger import ChatLogger
from modules import response_templates as tpl
from modules.utils import today_str

logger = logging.getLogger(__name__)


def _resp(text, buttons=None, cards=None, multi_select=None):
    """응답 dict 생성 헬퍼"""
    result = {"message": text}
    if buttons:
        result["buttons"] = buttons
    if cards:
        result["cards"] = cards
    if multi_select:
        result["multi_select"] = multi_select
    return result


class StepMachine:
    """대화 STEP 처리 엔진"""

    def __init__(self, state_store: StateStore, campaign_mgr: CampaignManager,
                 reviewer_mgr: ReviewerManager, chat_logger: ChatLogger,
                 web_url: str = "", ai_handler=None):
        self.states = state_store
        self.campaigns = campaign_mgr
        self.reviewers = reviewer_mgr
        self.chat_logger = chat_logger
        self.web_url = web_url
        self.ai_handler = ai_handler

    def process_message(self, name: str, phone: str, message: str):
        """메시지 처리 → 응답 반환 (str 또는 dict)"""
        state = self.states.get(name, phone)
        state.touch()  # 타임아웃 타이머 갱신

        self.chat_logger.log(state.reviewer_id, "user", message)

        try:
            response = self._dispatch(state, message)
        except Exception as e:
            logger.error(f"StepMachine 에러: {e}", exc_info=True)
            response = tpl.ERROR_OCCURRED

        # 로그는 텍스트만
        if isinstance(response, dict):
            self.chat_logger.log(state.reviewer_id, "bot", response.get("message", ""))
        else:
            self.chat_logger.log(state.reviewer_id, "bot", response)

        return response

    def get_welcome(self, name: str, phone: str):
        """접속 시 환영 메시지"""
        state = self.states.get(name, phone)
        # 캠페인 신청은 웹에서 진행 → 기존 채팅 세션 초기화
        if state.step in (1, 2, 3, 4, 5, 6, 7, 8):
            state.step = 0
            state.temp_data = {}
        return _resp(
            tpl.WELCOME_BACK.format(name=name),
            buttons=self._menu_buttons()
        )

    def _try_recover_session(self, state: ReviewerState) -> bool:
        """서버 재시작 후 DB에서 세션 복구 시도.
        가이드전달 상태(양식 미제출)만 복구 대상.
        구매캡쳐대기/리뷰대기는 사진 업로드(웹)로 진행하므로 복구 불필요."""
        try:
            if not self.reviewers or not self.reviewers.db:
                return False

            items = self.reviewers.db.search_by_name_phone(state.name, state.phone)

            # 가이드전달 상태만 복구 (양식 미제출 건)
            guide_sent = [
                item for item in items
                if item.get("상태") == "가이드전달"
            ]
            if not guide_sent:
                return False

            # 가장 최근 캠페인 기준
            campaign_id = guide_sent[0].get("캠페인ID", "")
            if not campaign_id:
                return False

            campaign = self.campaigns.get_campaign_by_id(campaign_id)
            if not campaign:
                return False

            # 같은 캠페인의 가이드전달 아이디들
            same_campaign = [
                item for item in guide_sent
                if item.get("캠페인ID") == campaign_id
            ]
            store_ids = [item.get("아이디", "") for item in same_campaign if item.get("아이디")]

            # 같은 캠페인에서 이미 양식 제출된 아이디도 포함
            all_campaign_items = [
                item for item in items
                if item.get("캠페인ID") == campaign_id
            ]
            submitted = [
                item.get("아이디") for item in all_campaign_items
                if item.get("상태") in ("구매캡쳐대기", "리뷰대기")
                and item.get("아이디")
            ]
            all_ids = list(set(store_ids + submitted))

            state.selected_campaign_id = campaign_id
            state.temp_data = {
                "campaign": campaign,
                "store_ids": all_ids,
                "submitted_ids": submitted,
                "account_count": len(all_ids),
            }
            state.step = 4  # 양식 입력 단계

            logger.info(f"세션 복구: {state.name} step={state.step} ids={store_ids}")
            return True
        except Exception as e:
            logger.error(f"세션 복구 실패: {e}")
            return False

    @staticmethod
    def _display_name(campaign: dict) -> str:
        """캠페인 표시명: 캠페인명이 있으면 캠페인명, 없으면 상품명"""
        return (campaign.get("캠페인명", "") or campaign.get("상품명", ""))

    def _menu_buttons(self):
        return [
            {"label": "내 진행현황", "value": "2"},
            {"label": "사진 제출", "value": "3"},
            {"label": "입금 확인", "value": "4"},
            {"label": "기타 문의", "value": "5"},
            {"label": "정보 수정", "value": "6"},
        ]

    def _back_button(self, value="__back__"):
        return {"label": "↩ 이전 단계", "value": value, "style": "secondary"}

    def _cancel_button(self):
        return {"label": "취소", "value": "__cancel__", "style": "danger"}

    def _build_resume_message(self, state: ReviewerState):
        """진행 중인 세션 복귀 안내"""
        campaign = state.temp_data.get("campaign", {})
        product = self._display_name(campaign)
        store_ids = state.temp_data.get("store_ids", [])
        submitted_ids = state.temp_data.get("submitted_ids", [])
        id_summary = ", ".join(store_ids) if store_ids else ""

        header = f"📌 진행 중인 신청이 있습니다.\n📦 {product}" if product else "📌 진행 중인 신청이 있습니다."

        if state.step == 1:
            cards = self.campaigns.build_campaign_cards(state.name, state.phone)
            return _resp(header + "\n\n캠페인을 선택해주세요.", cards=cards,
                         buttons=[self._cancel_button()])

        elif state.step == 2:
            return _resp(
                f"{header}\n\n몇 개 계정으로 진행하시겠습니까?",
                buttons=self._account_count_buttons()
            )

        elif state.step == 3:
            count = state.temp_data.get("account_count", 1)
            dup_state = state.temp_data.get("dup_state")
            if dup_state == "ask":
                valid_count = len(state.temp_data.get("valid_ids", []))
                dup_count = len(state.temp_data.get("dup_ids", []))
                return _resp(
                    f"{header}\n\n중복 아이디 처리 대기 중입니다.",
                    buttons=[
                        {"label": f"중복 제외 {valid_count}개로 진행", "value": "1"},
                        {"label": f"중복 {dup_count}개 대체", "value": "2"},
                        self._back_button(),
                    ]
                )
            if count == 1:
                return _resp(f"{header}\n\n스토어 아이디를 입력해주세요.",
                             buttons=self._prev_id_buttons(state) + [self._back_button()])
            campaign = state.temp_data.get("campaign", {})
            campaign_id = campaign.get("캠페인ID", "")
            ms_data = self._build_multi_select_data(state, campaign_id, count)
            if ms_data and ms_data["items"]:
                return _resp(
                    f"{header}\n\n아이디 {count}개를 선택해주세요.",
                    multi_select=ms_data,
                    buttons=[self._back_button()]
                )
            return _resp(f"{header}\n\n스토어 아이디 {count}개를 입력해주세요.\n(콤마로 구분)",
                         buttons=[self._back_button()])

        elif state.step in (4, 5):
            remaining = [sid for sid in store_ids if sid not in submitted_ids]
            if remaining:
                form_template = self._build_form_template(
                    campaign, state.name, state.phone, remaining
                )
                return _resp(
                    f"{header}\n🆔 {id_summary}\n\n"
                    f"⏳ 양식 미제출: {', '.join(remaining)}\n"
                    f"구매 후 양식을 제출해주세요:\n\n{form_template}",
                    buttons=[self._cancel_button()]
                )
            form_template = self._build_form_template(
                campaign, state.name, state.phone, store_ids
            )
            return _resp(f"{header}\n🆔 {id_summary}\n\n양식을 제출해주세요:\n\n{form_template}",
                         buttons=[self._cancel_button()])

        elif state.step == 6:
            upload_url = f"{self.web_url}/upload" if self.web_url else "/upload"
            return _resp(
                f"{header}\n🆔 {id_summary}\n\n"
                f"📸 구매 캡쳐를 제출해주세요.\n"
                f"🔗 사진 제출: {upload_url}",
                buttons=self._menu_buttons()
            )

        elif state.step == 7:
            upload_url = f"{self.web_url}/upload" if self.web_url else "/upload"
            deadline = state.temp_data.get("deadline", "확인 필요")
            return _resp(
                f"{header}\n🆔 {id_summary}\n\n"
                f"📸 리뷰 캡쳐를 제출해주세요.\n"
                f"🔗 사진 제출: {upload_url}\n"
                f"⏰ 리뷰 기한: {deadline}",
                buttons=self._menu_buttons()
            )

        elif state.step == 8:
            return _resp(tpl.ALL_DONE + "\n\n" + tpl.WELCOME_BACK.format(name=state.name),
                         buttons=self._menu_buttons())

        return _resp(tpl.WELCOME_BACK.format(name=state.name), buttons=self._menu_buttons())

    def _dispatch(self, state: ReviewerState, message: str):
        step = state.step
        msg = message.strip()

        # 글로벌 메뉴 복귀
        if msg in ("메뉴", "처음", "홈"):
            state.step = 0
            state.temp_data = {}
            return _resp(tpl.WELCOME_BACK.format(name=state.name), buttons=self._menu_buttons())

        # 글로벌 뒤로가기
        if msg == "__back__":
            return self._handle_back(state)

        # 글로벌 이어하기
        if msg == "__resume__":
            state.touch()
            return self._build_resume_message(state)

        # 글로벌 취소
        if msg == "__cancel__":
            return self._handle_cancel(state)

        # 글로벌 담당자 문의
        if msg == "__inquiry__":
            state.step = 10
            return _resp("담당자에게 전달할 문의 내용을 입력해주세요.",
                         buttons=[{"label": "↩ 메뉴로", "value": "메뉴"}])

        # 글로벌 정보 수정
        if msg == "__edit__":
            return self._enter_edit_mode(state)

        if step == 0:
            return self._step0_menu(state, msg)
        elif step in (1, 2, 3, 4, 5, 6, 7, 8):
            # 캠페인 신청/진행은 웹에서만 → 메뉴로 복귀
            state.step = 0
            state.temp_data = {}
            campaigns_url = f"{self.web_url}/campaigns" if self.web_url else "/campaigns"
            return _resp(
                f"캠페인 신청은 웹페이지에서 진행해주세요!\n🔗 {campaigns_url}",
                buttons=self._menu_buttons()
            )
        elif step == 9:
            return self._step9_inquiry_ai(state, msg)
        elif step == 10:
            return self._step10_inquiry_submit(state, msg)
        elif step == 11:
            return self._step11_edit_select(state, msg)
        elif step == 12:
            return self._step12_edit_field(state, msg)
        elif step == 13:
            return self._step13_edit_value(state, msg)
        else:
            state.step = 0
            return _resp(tpl.WELCOME_BACK.format(name=state.name), buttons=self._menu_buttons())

    # ─────────── 뒤로가기 / 취소 ───────────

    def _handle_back(self, state: ReviewerState):
        step = state.step

        if step <= 1:
            # 캠페인 목록으로
            state.step = 0
            state.temp_data = {}
            return _resp(tpl.WELCOME_BACK.format(name=state.name), buttons=self._menu_buttons())

        elif step == 2:
            state.step = 1
            cards = self.campaigns.build_campaign_cards(state.name, state.phone)
            return _resp("캠페인을 선택해주세요.", cards=cards)

        elif step == 3:
            state.step = 2
            campaign = state.temp_data.get("campaign", {})
            self._clear_dup_state(state)
            return _resp(
                tpl.ASK_ACCOUNT_COUNT.format(
                    product_name=self._display_name(campaign),
                    store_name=campaign.get("업체명", ""),
                ),
                buttons=self._account_count_buttons()
            )

        elif step == 4:
            state.step = 3
            count = state.temp_data.get("account_count", 1)
            state.temp_data["store_ids"] = []
            state.temp_data.pop("submitted_ids", None)
            if count == 1:
                return _resp(
                    "스토어 아이디를 입력해주세요.",
                    buttons=self._prev_id_buttons(state) + [self._back_button()]
                )
            campaign = state.temp_data.get("campaign", {})
            campaign_id = campaign.get("캠페인ID", "")
            ms_data = self._build_multi_select_data(state, campaign_id, count)
            if ms_data and ms_data["items"]:
                return _resp(
                    f"아이디 {count}개를 선택해주세요.\n이전에 사용했던 아이디를 선택하거나 신규 아이디를 추가해주세요.",
                    multi_select=ms_data,
                    buttons=[self._back_button()]
                )
            return _resp(
                f"스토어 아이디 {count}개를 입력해주세요.\n(콤마로 구분)",
                buttons=[self._back_button()]
            )

        elif step == 5:
            # 양식 입력 중 뒤로가기 → 취소 확인
            return self._handle_cancel(state)

        else:
            state.step = 0
            state.temp_data = {}
            return _resp(tpl.WELCOME_BACK.format(name=state.name), buttons=self._menu_buttons())

    def _handle_cancel(self, state: ReviewerState):
        """진행 취소 처리"""
        # 이미 취소 확인 대기중?
        if state.temp_data.get("cancel_confirm"):
            state.temp_data.pop("cancel_confirm", None)
            return self._do_cancel(state)

        # 양식 제출 전이면 바로 취소
        submitted = state.temp_data.get("submitted_ids", [])
        if not submitted and state.step <= 4:
            return self._do_cancel(state)

        # 양식 제출 후면 확인
        state.temp_data["cancel_confirm"] = True
        return _resp(
            "진행을 취소하시겠어요?\n양식이 접수된 건은 시트에서 취소 처리됩니다.",
            buttons=[
                {"label": "취소하고 캠페인 목록으로", "value": "__cancel__", "style": "danger"},
                {"label": "계속 진행하기", "value": "__continue__"},
            ]
        )

    def _do_cancel(self, state: ReviewerState):
        """실제 취소 수행"""
        campaign = state.temp_data.get("campaign", {})
        campaign_id = campaign.get("캠페인ID", "")
        store_ids = state.temp_data.get("store_ids", [])

        if campaign_id and store_ids:
            try:
                self.reviewers.db.cancel_by_timeout(
                    state.name, state.phone, campaign_id, store_ids
                )
            except Exception as e:
                logger.error(f"취소 처리 에러: {e}")

        state.step = 0
        state.temp_data = {}
        cards = self.campaigns.build_campaign_cards(state.name, state.phone)
        return _resp(
            "취소 처리되었습니다. 다른 캠페인을 확인하시겠어요?",
            cards=cards,
            buttons=self._menu_buttons()
        )

    # ─────────── STEP 0: 메뉴 ───────────

    def _step0_menu(self, state: ReviewerState, message: str):
        # __continue__ 처리 (취소 확인에서 계속 진행)
        if message == "__continue__":
            state.temp_data.pop("cancel_confirm", None)
            return self._build_resume_message(state)

        # 진행현황 전체 보기
        if message == "__more_status__":
            items = self.reviewers.get_items(state.name, state.phone)
            return _resp(self._format_status(items), buttons=self._menu_buttons())

        choice = parse_menu_choice(message)

        if choice == 1:
            campaigns_url = f"{self.web_url}/campaigns" if self.web_url else "/campaigns"
            return _resp(
                f"캠페인 신청은 웹페이지에서 진행해주세요!\n🔗 {campaigns_url}",
                buttons=self._menu_buttons()
            )

        elif choice == 2:
            items = self.reviewers.get_items(state.name, state.phone)
            if not items["in_progress"] and not items["completed"]:
                return _resp("진행 중인 체험단이 없습니다. 체험단을 신청해보세요!",
                             buttons=self._menu_buttons())
            total_count = len(items["in_progress"]) + len(items["completed"])
            text = self._format_status(items, limit=5)
            buttons = self._menu_buttons()
            if total_count > 5:
                buttons = [{"label": f"전체 보기 ({total_count}건)", "value": "__more_status__"}] + buttons
            return _resp(text, buttons=buttons)

        elif choice == 3:
            upload_url = f"{self.web_url}/upload" if self.web_url else "/upload"
            return _resp(
                f"📸 사진 제출은 아래 링크에서 가능합니다:\n🔗 {upload_url}\n\n또는 ☰ 메뉴 → 사진제출",
                buttons=self._menu_buttons()
            )

        elif choice == 4:
            payments = self.reviewers.get_payments(state.name, state.phone)
            return _resp(self._format_payments(payments), buttons=self._menu_buttons())

        elif choice == 5:
            state.step = 9
            return _resp("궁금한 점을 자유롭게 입력해주세요! 😊\nAI가 답변드리고, 필요하면 담당자에게 연결해드릴게요.",
                         buttons=[{"label": "↩ 메뉴로", "value": "메뉴"}])

        elif choice == 6:
            return self._enter_edit_mode(state)

        # 주문번호/양식 데이터를 단독으로 보낸 경우 안내
        import re
        if re.match(r"^(주문번호|주문\s*번호)\s*[:：]?\s*\d", message) or re.match(r"^\d{10,}$", message.strip()):
            return _resp(
                "주문번호는 양식과 함께 제출해주세요.\n"
                "양식 입력 단계에서 모든 항목을 한 번에 보내주시면 됩니다.",
                buttons=self._menu_buttons()
            )

        return self._ask_ai(state, message)

    # ─────────── STEP 1: 캠페인 선택 (카드) ───────────

    def _step1_campaign(self, state: ReviewerState, message: str):
        # 카드 버튼에서 campaign_N 형식으로 전달됨
        choice = None
        if message.startswith("campaign_"):
            try:
                choice = int(message.replace("campaign_", ""))
            except ValueError:
                pass
        if choice is None:
            choice = parse_campaign_choice(message)
        if choice is None:
            cards = self.campaigns.build_campaign_cards(state.name, state.phone)
            return _resp("캠페인을 선택해주세요.", cards=cards)

        campaign = self.campaigns.get_campaign_by_index(choice)
        if not campaign:
            cards = self.campaigns.build_campaign_cards(state.name, state.phone)
            return _resp("해당 번호의 캠페인이 없습니다. 다시 선택해주세요.", cards=cards)

        # 구매가능시간 체크
        display = self._display_name(campaign)
        if not campaign.get("_buy_time_active", True):
            buy_time = campaign.get("구매가능시간", "")
            cards = self.campaigns.build_campaign_cards(state.name, state.phone)
            return _resp(
                f"'{display}' 캠페인은 구매 가능 시간이 아닙니다.\n⏰ 진행시간: {buy_time}",
                cards=cards
            )

        # 금일 모집목표 도달 체크
        if self.campaigns.is_daily_full(campaign):
            cards = self.campaigns.build_campaign_cards(state.name, state.phone)
            return _resp(
                f"'{display}' 캠페인은 오늘 모집이 마감되었습니다.\n내일 다시 신청해주세요!",
                cards=cards
            )

        # 재구매 체크
        try:
            repurchase = self.campaigns.db.check_repurchase(
                state.name, state.phone, campaign.get("캠페인ID", "")
            )
            if repurchase:
                prev = repurchase[0]
                return _resp(
                    f"⚠️ 이 상품은 이전에 '{prev['product_name']}' 캠페인에서 구매한 이력이 있습니다.\n"
                    f"재구매로 판별될 수 있어 신청이 제한됩니다.",
                    buttons=self._menu_buttons()
                )
        except Exception:
            pass

        state.selected_campaign_id = campaign.get("캠페인ID", str(choice))
        state.temp_data["campaign"] = campaign
        state.temp_data["store_ids"] = []
        state.step = 2

        return _resp(
            tpl.ASK_ACCOUNT_COUNT.format(
                product_name=self._display_name(campaign),
                store_name=campaign.get("업체명", ""),
            ),
            buttons=self._account_count_buttons()
        )

    # ─────────── STEP 2: 계정 수 (버튼) ───────────

    def _account_count_buttons(self):
        return [
            {"label": "1개", "value": "1"},
            {"label": "2개", "value": "2"},
            {"label": "3개", "value": "3"},
            {"label": "직접 입력", "value": "__direct_count__"},
            self._back_button(),
        ]

    def _step2_account_count(self, state: ReviewerState, message: str):
        if message == "__direct_count__":
            return _resp("몇 개 계정으로 진행할지 숫자를 입력해주세요. (1~10)",
                         buttons=[self._back_button()])

        try:
            count = int(message)
        except ValueError:
            return _resp("숫자를 입력해주세요.",
                         buttons=self._account_count_buttons())

        if count < 1 or count > 10:
            return _resp("1~10 사이의 숫자를 입력해주세요.",
                         buttons=self._account_count_buttons())

        state.temp_data["account_count"] = count
        state.temp_data["store_ids"] = []
        state.step = 3

        if count == 1:
            return _resp(
                "스토어 아이디를 입력해주세요.",
                buttons=self._prev_id_buttons(state) + [self._back_button()]
            )

        campaign = state.temp_data.get("campaign", {})
        campaign_id = campaign.get("캠페인ID", "")
        ms_data = self._build_multi_select_data(state, campaign_id, count)
        if ms_data and ms_data["items"]:
            return _resp(
                f"아이디 {count}개를 선택해주세요.\n이전에 사용했던 아이디를 선택하거나 신규 아이디를 추가해주세요.",
                multi_select=ms_data,
                buttons=[self._back_button()]
            )
        return _resp(
            f"스토어 아이디 {count}개를 입력해주세요.\n(콤마로 구분. 예: abc123, def456)",
            buttons=[self._back_button()]
        )

    # ─────────── STEP 3: 아이디 수집 ───────────

    def _build_multi_select_data(self, state: ReviewerState, campaign_id: str, max_select: int):
        """다중 선택용 이전 아이디 데이터 (2개 이상 선택 시)"""
        try:
            if not self.reviewers or not self.reviewers.db:
                return None

            # 이 리뷰어의 사용 아이디 수집
            used_ids = self.reviewers.db.get_used_store_ids(state.name, state.phone)
            if not used_ids:
                return None

            # 이 캠페인에서 이미 진행중인 아이디
            campaign = state.temp_data.get("campaign", {})
            allow_dup = campaign.get("중복허용", "").strip().upper() in ("Y", "O", "예", "허용")
            active_ids = set()

            if not allow_dup and campaign_id:
                active_ids = self.reviewers.db.get_active_ids_for_campaign(
                    state.name, state.phone, campaign_id
                )

            # 동시진행그룹 내 다른 캠페인에서 사용 중인 아이디
            group_ids = set()
            if campaign_id:
                group_ids = self.reviewers.db.get_exclusive_group_active_ids(campaign_id)

            items = []
            for sid in sorted(used_ids)[:8]:
                if sid in active_ids:
                    items.append({"id": sid, "disabled": True, "reason": "진행중"})
                elif sid in group_ids:
                    items.append({"id": sid, "disabled": True, "reason": "동시진행중"})
                else:
                    items.append({"id": sid, "disabled": False, "reason": ""})

            return {
                "max_select": max_select,
                "items": items,
            }
        except Exception:
            return None

    def _prev_id_buttons(self, state: ReviewerState):
        """이전에 사용한 아이디 버튼 목록"""
        try:
            if not self.reviewers or not self.reviewers.db:
                return []
            used_ids = self.reviewers.db.get_used_store_ids(state.name, state.phone)
            if not used_ids:
                return []
            buttons = []
            for sid in sorted(used_ids)[:5]:
                buttons.append({"label": f"{sid} 사용", "value": sid})
            buttons.append({"label": "+ 신규 아이디 입력", "value": "__new_id__"})
            return buttons
        except Exception:
            return []

    def _step3_collect_ids(self, state: ReviewerState, message: str):
        raw = message.strip()

        # 다중 선택 UI에서 전달된 경우
        if raw.startswith("__ms__"):
            raw = raw[6:]

        if raw == "__new_id__":
            return _resp("신규 아이디를 입력해주세요.", buttons=[self._back_button()])

        if not raw:
            return _resp(tpl.ASK_STORE_IDS, buttons=[self._back_button()])

        campaign = state.temp_data.get("campaign", {})
        campaign_id = campaign.get("캠페인ID", "")
        account_count = state.temp_data.get("account_count", 1)

        # ── 중복 처리 서브스테이트 ──
        dup_state = state.temp_data.get("dup_state")

        if dup_state == "ask":
            if raw in ("1", "1번"):
                valid_ids = state.temp_data.get("valid_ids", [])
                state.temp_data["store_ids"] = valid_ids
                state.temp_data["account_count"] = len(valid_ids)
                self._clear_dup_state(state)
                return self._register_and_guide(state)
            elif raw in ("2", "2번"):
                state.temp_data["dup_state"] = "replace"
                dup_count = len(state.temp_data.get("dup_ids", []))
                return _resp(f"대체할 아이디 {dup_count}개를 입력해주세요. (콤마로 구분)",
                             buttons=[self._back_button()])
            else:
                valid_count = len(state.temp_data.get("valid_ids", []))
                dup_count = len(state.temp_data.get("dup_ids", []))
                return _resp(
                    "어떻게 진행하시겠습니까?",
                    buttons=[
                        {"label": f"중복 제외 {valid_count}개로 진행", "value": "1"},
                        {"label": f"중복 {dup_count}개 대체", "value": "2"},
                        self._back_button(),
                    ]
                )

        if dup_state == "replace":
            new_ids = [x.strip() for x in re.split(r'[,\s]+', raw) if x.strip()]
            dup_count = len(state.temp_data.get("dup_ids", []))
            valid_ids = state.temp_data.get("valid_ids", [])

            if len(new_ids) != dup_count:
                return _resp(f"⚠️ {dup_count}개 아이디를 입력해주세요. (현재 {len(new_ids)}개)")

            if len(new_ids) != len(set(new_ids)):
                return _resp("⚠️ 중복된 아이디가 있습니다. 다시 입력해주세요.")

            overlap = [sid for sid in new_ids if sid in valid_ids]
            if overlap:
                return _resp(f"⚠️ '{overlap[0]}'은(는) 이미 사용 가능한 아이디에 포함되어 있습니다.")

            allow_dup = campaign.get("중복허용", "").strip().upper() in ("Y", "O", "예", "허용")
            if not allow_dup:
                for sid in new_ids:
                    is_dup = self.reviewers.check_duplicate(campaign_id, sid)
                    if is_dup:
                        return _resp(
                            tpl.DUPLICATE_FOUND.format(store_id=sid) +
                            f"\n\n대체할 아이디 {dup_count}개를 다시 입력해주세요."
                        )

            # 동시진행그룹 중복 체크
            for sid in new_ids:
                conflict = self.reviewers.db.check_exclusive_group_duplicate(campaign_id, sid)
                if conflict:
                    return _resp(
                        f"⚠️ '{sid}'은(는) 동시진행 캠페인 [{conflict}]에서 사용 중입니다.\n\n"
                        f"대체할 아이디 {dup_count}개를 다시 입력해주세요."
                    )

            all_ids = valid_ids + new_ids
            state.temp_data["store_ids"] = all_ids
            self._clear_dup_state(state)
            return self._register_and_guide(state)

        # ── 일반 ID 입력 처리 ──
        ids = [x.strip() for x in re.split(r'[,\s]+', raw) if x.strip()]

        if not ids:
            return _resp("아이디를 입력해주세요.", buttons=[self._back_button()])

        if len(ids) != account_count:
            return _resp(
                f"⚠️ {account_count}개 아이디를 입력해주세요. (현재 {len(ids)}개 입력됨)\n콤마로 구분하여 입력해주세요.",
                buttons=[self._back_button()]
            )

        if len(ids) != len(set(ids)):
            return _resp("⚠️ 중복된 아이디가 있습니다. 다시 입력해주세요.",
                         buttons=[self._back_button()])

        allow_dup = campaign.get("중복허용", "").strip().upper() in ("Y", "O", "예", "허용")
        if not allow_dup:
            dup_ids = []
            valid_ids = []
            for sid in ids:
                is_dup = self.reviewers.check_duplicate(campaign_id, sid)
                if is_dup:
                    dup_ids.append(sid)
                else:
                    valid_ids.append(sid)

            if dup_ids:
                if not valid_ids:
                    dup_list = ", ".join(dup_ids)
                    return _resp(
                        f"⚠️ 입력하신 아이디가 모두 중복입니다: {dup_list}\n다시 입력해주세요.",
                        buttons=[self._back_button()]
                    )

                dup_list = ", ".join(dup_ids)
                valid_list = ", ".join(valid_ids)
                state.temp_data["dup_state"] = "ask"
                state.temp_data["dup_ids"] = dup_ids
                state.temp_data["valid_ids"] = valid_ids

                return _resp(
                    f"⚠️ 중복된 아이디: {dup_list}\n"
                    f"✅ 사용 가능한 아이디: {valid_list}\n\n"
                    f"어떻게 진행하시겠습니까?",
                    buttons=[
                        {"label": f"중복 제외 {len(valid_ids)}개로 진행", "value": "1"},
                        {"label": f"중복 {len(dup_ids)}개 대체", "value": "2"},
                        self._back_button(),
                    ]
                )

        # 동시진행그룹 중복 체크 (그룹 내 다른 캠페인에서 사용중인 아이디 차단)
        group_blocked = []
        for sid in ids:
            conflict = self.reviewers.db.check_exclusive_group_duplicate(campaign_id, sid)
            if conflict:
                group_blocked.append((sid, conflict))
        if group_blocked:
            blocked_lines = [f"  - {sid} → [{cname}]에서 진행중" for sid, cname in group_blocked]
            remaining = [sid for sid in ids if sid not in {s for s, _ in group_blocked}]
            msg = "⚠️ 동시진행 캠페인에서 이미 사용 중인 아이디입니다:\n" + "\n".join(blocked_lines)
            if remaining:
                msg += f"\n\n사용 가능: {', '.join(remaining)}"
            msg += "\n\n다른 아이디로 다시 입력해주세요."
            return _resp(msg, buttons=[self._back_button()])

        state.temp_data["store_ids"] = ids
        return self._register_and_guide(state)

    def _clear_dup_state(self, state: ReviewerState):
        state.temp_data.pop("dup_state", None)
        state.temp_data.pop("dup_ids", None)
        state.temp_data.pop("valid_ids", None)

    def _register_and_guide(self, state: ReviewerState):
        """아이디 등록 + 옵션 선택 또는 가이드 전달"""
        campaign = state.temp_data.get("campaign", {})
        campaign_id = campaign.get("캠페인ID", "")
        ids = state.temp_data.get("store_ids", [])

        # 정원 초과 체크 (취소 제외 전체 슬롯)
        available = self.campaigns.check_capacity(campaign_id)
        if available < len(ids):
            state.step = 0
            state.temp_data = {}
            if available == 0:
                return _resp(
                    "😥 죄송합니다, 이 캠페인은 모집이 마감되었습니다.\n다른 캠페인을 확인해보세요!",
                    buttons=self._menu_buttons()
                )
            return _resp(
                f"😥 죄송합니다, 남은 자리가 {available}자리뿐입니다.\n"
                f"{len(ids)}개 아이디로 신청하실 수 없습니다. 다시 시도해주세요.",
                buttons=self._menu_buttons()
            )

        # 일일 모집한도 체크
        daily_remaining = self.campaigns.check_daily_remaining(campaign_id)
        if daily_remaining != -1 and daily_remaining < len(ids):
            state.step = 0
            state.temp_data = {}
            if daily_remaining == 0:
                return _resp(
                    "😥 죄송합니다, 오늘 모집이 마감되었습니다.\n내일 다시 신청해주세요!",
                    buttons=self._menu_buttons()
                )
            return _resp(
                f"😥 죄송합니다, 오늘 남은 자리가 {daily_remaining}자리뿐입니다.\n"
                f"{len(ids)}개 아이디로 신청하실 수 없습니다. 다시 시도해주세요.",
                buttons=self._menu_buttons()
            )

        # 시트에 등록
        for sid in ids:
            self.reviewers.register(state.name, state.phone, campaign, sid)
        for sid in ids:
            self._update_status_by_id(state.name, state.phone, campaign_id, sid, "가이드전달")

        # 리뷰어DB 아이디목록 업데이트
        try:
            for sid in ids:
                self.reviewers.db.update_reviewer_store_ids(state.name, state.phone, sid)
        except Exception:
            pass

        # 사진 세트 자동 할당 (계정/progress 단위)
        try:
            photo_sets = self.reviewers.db.get_campaign_photo_sets(campaign_id)
            if photo_sets:
                reviewer_obj = self.reviewers.db.get_reviewer(state.name, state.phone)
                if reviewer_obj:
                    pid_rows = self.reviewers.db._fetchall(
                        """SELECT id FROM progress
                           WHERE reviewer_id = %s AND campaign_id = %s
                           AND status NOT IN ('타임아웃취소', '취소')
                           AND photo_set_number IS NULL""",
                        (reviewer_obj["id"], campaign_id),
                    )
                    for pr in pid_rows:
                        next_set = self.reviewers.db.get_next_photo_set_number(campaign_id)
                        if next_set is None:
                            break
                        self.reviewers.db.assign_photo_set([pr["id"]], next_set)
                        logger.info("사진세트 %d 할당: %s (progress %d)", next_set, state.name, pr["id"])
        except Exception as e:
            logger.warning("사진세트 자동할당 실패: %s", e)

        # 서버PC에 친구추가 요청 (이미 친구가 아닌 경우만)
        try:
            reviewer = self.reviewers.db.get_reviewer(state.name, state.phone)
            if not reviewer or not reviewer.get("kakao_friend"):
                from modules.signal_sender import request_friend_add
                request_friend_add(state.name, state.phone)
        except Exception:
            pass

        # 모집 완료 자동 처리: 총수량 or 일일한도 꽉 차면 홍보 태스크 취소
        try:
            from modules.signal_sender import cancel_campaign_tasks
            remaining = self.campaigns.check_capacity(campaign_id)
            if remaining <= 0:
                import models as _models
                if _models.db_manager:
                    _models.db_manager.update_campaign_status(campaign_id, "모집마감")
                    logger.info("캠페인 [%s] 모집마감 자동 전환", campaign_id)
                cancel_campaign_tasks(campaign_id)
            elif self.campaigns.check_daily_remaining(campaign_id) == 0:
                logger.info("캠페인 [%s] 일일모집 마감 → 홍보 태스크 취소", campaign_id)
                cancel_campaign_tasks(campaign_id)
        except Exception as e:
            logger.warning("모집마감 자동처리 실패: %s", e)

        state.temp_data["submitted_ids"] = []
        id_summary = ", ".join(ids)
        confirm = f"✅ 아이디 확인: {id_summary}"

        # 옵션 분기 처리
        options = self._parse_campaign_options(campaign)
        if options and len(options) > 1 and len(ids) > 0:
            # 다중 옵션 → 아이디별 옵션 선택
            state.step = 4
            state.temp_data["option_selection"] = {}
            state.temp_data["option_pending_ids"] = list(ids)
            state.temp_data["options"] = options

            current_id = ids[0]
            option_buttons = []
            for opt in options:
                price_str = f" - {int(opt['price']):,}원" if opt.get("price") else ""
                option_buttons.append({
                    "label": f"{opt['name']}{price_str}",
                    "value": f"__option__{opt['name']}",
                })
            option_buttons.append(self._back_button())

            return _resp(
                f"{confirm}\n\n📌 {current_id}의 옵션을 선택해주세요:",
                buttons=option_buttons
            )

        # 단일 옵션이면 바로 가이드 전달
        state.step = 4

        guide = self._build_purchase_guide(campaign, state.name, state.phone, ids)
        if len(ids) > 1:
            return _resp(
                f"{confirm}\n\n{guide}\n\n"
                f"📋 위 가이드를 참고하여 {len(ids)}개 계정 양식을 각각 제출해주세요.",
                buttons=[self._cancel_button()]
            )
        return _resp(f"{confirm}\n\n{guide}", buttons=[self._cancel_button()])

    def _parse_campaign_options(self, campaign: dict) -> list[dict]:
        """캠페인의 옵션목록 파싱 (JSON 또는 슬래시 구분)"""
        option_mode = campaign.get("옵션지정방식", "").strip()
        if option_mode not in ("세부지정",):
            return []

        raw = campaign.get("옵션목록", "").strip()
        if not raw:
            return []

        # JSON 형식 시도
        try:
            options = json.loads(raw)
            if isinstance(options, list):
                return options
        except (json.JSONDecodeError, TypeError):
            pass

        # 콤마+슬래시 형식: "들기름300ml/12900, 들기름500ml/18900"
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        options = []
        for part in parts:
            if "/" in part:
                name, price = part.rsplit("/", 1)
                try:
                    options.append({"name": name.strip(), "price": int(price.strip())})
                except ValueError:
                    options.append({"name": part, "price": 0})
            else:
                options.append({"name": part, "price": 0})
        return options if len(options) > 1 else []

    # ─────────── STEP 4: 옵션 선택 + 가이드 전달 ───────────

    def _step4_guide_and_form(self, state: ReviewerState, message: str):
        """옵션 선택 또는 양식 파싱"""
        # 옵션 선택 모드
        if state.temp_data.get("option_pending_ids"):
            return self._handle_option_selection(state, message)

        # 양식 입력이 온 경우
        return self._step5_form(state, message)

    def _handle_option_selection(self, state: ReviewerState, message: str):
        """아이디별 옵션 선택 처리"""
        campaign = state.temp_data.get("campaign", {})
        pending_ids = state.temp_data.get("option_pending_ids", [])
        options = state.temp_data.get("options", [])
        option_selection = state.temp_data.get("option_selection", {})

        if not pending_ids:
            # 모든 옵션 선택 완료
            return self._finalize_option_selection(state)

        current_id = pending_ids[0]

        # 옵션 값 파싱
        selected_option = None
        if message.startswith("__option__"):
            opt_name = message.replace("__option__", "")
            for opt in options:
                if opt["name"] == opt_name:
                    selected_option = opt
                    break

        if not selected_option:
            # 텍스트로 옵션명 입력 시도
            for opt in options:
                if opt["name"] in message:
                    selected_option = opt
                    break

        if not selected_option:
            option_buttons = []
            for opt in options:
                price_str = f" - {int(opt['price']):,}원" if opt.get("price") else ""
                option_buttons.append({
                    "label": f"{opt['name']}{price_str}",
                    "value": f"__option__{opt['name']}",
                })
            option_buttons.append(self._back_button())
            return _resp(f"📌 {current_id}의 옵션을 선택해주세요:", buttons=option_buttons)

        # 옵션 선택 저장
        option_selection[current_id] = selected_option
        state.temp_data["option_selection"] = option_selection
        pending_ids.pop(0)
        state.temp_data["option_pending_ids"] = pending_ids

        if pending_ids:
            # 다음 아이디 옵션 선택
            next_id = pending_ids[0]
            option_buttons = []
            for opt in options:
                price_str = f" - {int(opt['price']):,}원" if opt.get("price") else ""
                option_buttons.append({
                    "label": f"{opt['name']}{price_str}",
                    "value": f"__option__{opt['name']}",
                })
            option_buttons.append(self._back_button())
            return _resp(
                f"✅ {current_id} → {selected_option['name']}\n\n📌 {next_id}의 옵션을 선택해주세요:",
                buttons=option_buttons
            )

        return self._finalize_option_selection(state)

    def _finalize_option_selection(self, state: ReviewerState):
        """옵션 선택 완료 → 가이드 전달"""
        campaign = state.temp_data.get("campaign", {})
        ids = state.temp_data.get("store_ids", [])
        option_selection = state.temp_data.get("option_selection", {})

        # 선택된 옵션 요약
        parts = []
        for sid in ids:
            opt = option_selection.get(sid, {})
            if opt:
                parts.append(f"  {sid} → {opt.get('name', '')}")

        summary = "\n".join(parts)
        guide = self._build_purchase_guide(campaign, state.name, state.phone, ids, option_selection)

        if len(ids) > 1:
            return _resp(
                f"✅ 옵션 선택 완료:\n{summary}\n\n{guide}\n\n"
                f"📋 위 가이드를 참고하여 {len(ids)}개 계정 양식을 각각 제출해주세요.",
                buttons=[self._cancel_button()]
            )
        return _resp(f"✅ 옵션 선택 완료:\n{summary}\n\n{guide}",
                     buttons=[self._cancel_button()])

    # ─────────── STEP 5: 양식 접수 ───────────

    def _step5_form(self, state: ReviewerState, message: str):
        # __continue__ 처리
        if message == "__continue__":
            state.temp_data.pop("cancel_confirm", None)
            return self._build_resume_message(state)

        campaign = state.temp_data.get("campaign", {})
        store_ids = state.temp_data.get("store_ids", [])
        submitted_ids = state.temp_data.get("submitted_ids", [])
        remaining_ids = [sid for sid in store_ids if sid not in submitted_ids]

        if not campaign or not store_ids:
            state.step = 0
            return _resp(
                "캠페인 정보가 없습니다. 처음부터 다시 진행해주세요.",
                buttons=self._menu_buttons()
            )

        # 다중 양식 감지
        forms = parse_multiple_forms(message)

        if not forms:
            parsed = parse_full_form(message)
            required = ["수취인명", "연락처", "은행", "계좌", "예금주", "주소", "주문번호", "결제금액"]
            missing = [f for f in required if not parsed.get(f)]

            if len(missing) == len(required):
                form_template = self._build_form_template(
                    campaign, state.name, state.phone, remaining_ids
                )
                return _resp(
                    f"구매 완료 후 양식을 입력해주세요.\n\n{form_template}",
                    buttons=[self._cancel_button()]
                )
            missing_text = "\n".join(f"- {f}" for f in missing)
            form_template = self._build_form_template(
                campaign, state.name, state.phone, remaining_ids
            )
            return _resp(
                tpl.FORM_MISSING_FIELDS.format(
                    missing_list=missing_text,
                    form_template=form_template,
                ),
                buttons=[self._cancel_button()]
            )

        # 각 양식 처리
        campaign_id = campaign.get("캠페인ID", "")
        results = []
        errors = []

        for parsed in forms:
            required = ["수취인명", "연락처", "은행", "계좌", "예금주", "주소", "주문번호", "결제금액"]
            missing = [f for f in required if not parsed.get(f)]

            if missing:
                form_id = parsed.get("아이디", "?")
                errors.append(f"[{form_id}] 누락: {', '.join(missing)}")
                continue

            form_id = parsed.get("아이디", "").strip()

            if len(remaining_ids) == 1 and not form_id:
                target_id = remaining_ids[0]
            elif form_id and form_id in remaining_ids:
                target_id = form_id
            elif form_id:
                errors.append(f"'{form_id}'은(는) 미제출 아이디 목록에 없습니다.")
                continue
            else:
                errors.append("아이디가 입력되지 않은 양식이 있습니다.")
                continue

            self.reviewers.update_form_data(
                state.name, state.phone, campaign_id, target_id, parsed,
                campaign=campaign,
            )
            # 양식 제출 → 구매캡쳐대기
            self._update_status_by_id(state.name, state.phone, campaign_id, target_id, "구매캡쳐대기")

            submitted_ids.append(target_id)
            remaining_ids = [sid for sid in store_ids if sid not in submitted_ids]
            results.append(target_id)

        state.temp_data["submitted_ids"] = submitted_ids

        response_parts = []

        if results:
            confirmed = ", ".join(results)
            response_parts.append(f"✅ 양식 접수 완료: {confirmed}")

        if errors:
            error_text = "\n".join(f"⚠️ {e}" for e in errors)
            response_parts.append(error_text)

        new_remaining = [sid for sid in store_ids if sid not in submitted_ids]
        upload_url = f"{self.web_url}/upload" if self.web_url else "/upload"

        if new_remaining:
            form_template = self._build_form_template(
                campaign, state.name, state.phone, new_remaining
            )
            response_parts.append(
                f"\n⏳ 남은 아이디: {', '.join(new_remaining)}\n"
                f"다음 양식을 제출해주세요:\n\n{form_template}"
            )
            return _resp("\n\n".join(response_parts), buttons=[self._cancel_button()])

        if not results:
            form_template = self._build_form_template(
                campaign, state.name, state.phone, new_remaining or store_ids
            )
            response_parts.append(f"\n양식을 다시 제출해주세요:\n\n{form_template}")
            return _resp("\n\n".join(response_parts), buttons=[self._cancel_button()])

        # 모든 아이디 양식 제출 완료 → step 6
        state.step = 6
        id_list = ", ".join(store_ids)
        last_parsed = forms[-1] if forms else {}

        response_parts.append(
            tpl.FORM_RECEIVED.format(
                product_name=self._display_name(campaign),
                id_list=id_list,
                recipient_name=last_parsed.get("수취인명", state.name),
                upload_url=upload_url,
            )
        )
        return _resp("\n\n".join(response_parts))

    # ─────────── STEP 6: 구매캡쳐 대기 ───────────

    def _step6_purchase(self, state: ReviewerState, message: str):
        choice = parse_menu_choice(message)
        if choice:
            state.step = 0
            return self._step0_menu(state, message)

        return self._ask_ai(state, message)

    # ─────────── STEP 7: 리뷰캡쳐 대기 ───────────

    def _step7_review(self, state: ReviewerState, message: str):
        choice = parse_menu_choice(message)
        if choice:
            state.step = 0
            return self._step0_menu(state, message)

        return self._ask_ai(state, message)

    # ─────────── STEP 8: 완료 ───────────

    def _step8_done(self, state: ReviewerState, message: str):
        state.step = 0
        return _resp(tpl.ALL_DONE + "\n\n" + tpl.WELCOME_BACK.format(name=state.name),
                     buttons=self._menu_buttons())

    # ─────────── 시트 상태 업데이트 헬퍼 ───────────

    def _update_status_by_id(self, name, phone, campaign_id, store_id, new_status):
        try:
            if not self.reviewers or not self.reviewers.db:
                return
            self.reviewers.db.update_status_by_id(name, phone, campaign_id, store_id, new_status)
        except Exception as e:
            logger.error(f"상태 업데이트 에러: {e}")

    # ─────────── 구매 가이드 빌더 (조건부 생성) ───────────

    def _build_form_template(self, campaign: dict, name: str, phone: str,
                              store_ids: list = None) -> str:
        prev_info = {}
        try:
            if self.reviewers and self.reviewers.db:
                prev_info = self.reviewers.db.get_user_prev_info(name, phone)
        except Exception as e:
            logger.error(f"기존 정보 조회 에러: {e}")

        guide_amount = campaign.get("결제금액", "") if campaign else ""

        # 다중 아이디: 각각 양식 생성
        if store_ids and len(store_ids) > 1:
            all_forms = []
            for sid in store_ids:
                lines = [
                    f"아이디: {sid}",
                    "수취인명: ",
                    "연락처: ",
                    f"결제금액: {guide_amount}",
                    "주문번호: ",
                    f"은행: {prev_info.get('은행', '')}",
                    f"계좌: {prev_info.get('계좌', '')}",
                    f"예금주: {prev_info.get('예금주', '')}",
                    f"주소: {prev_info.get('주소', '')}",
                ]
                all_forms.append("\n".join(lines))
            return "\n\n---\n\n".join(all_forms)

        lines = []
        if store_ids and len(store_ids) == 1:
            lines.append(f"아이디: {store_ids[0]}")

        lines += [
            "수취인명: ",
            "연락처: ",
            f"결제금액: {guide_amount}",
            "주문번호: ",
            f"은행: {prev_info.get('은행', '')}",
            f"계좌: {prev_info.get('계좌', '')}",
            f"예금주: {prev_info.get('예금주', '')}",
            f"주소: {prev_info.get('주소', '')}",
        ]
        return "\n".join(lines)

    def _build_purchase_guide(self, campaign: dict, name: str, phone: str,
                              store_ids: list = None, option_selection: dict = None) -> str:
        """조건부 구매 가이드 자동 생성"""
        form_template = self._build_form_template(campaign, name, phone, store_ids)

        # 상품이미지 태그 (채팅 프론트엔드에서 렌더링)
        product_image = campaign.get("상품이미지", "").strip()
        image_tag = f"[IMG:{product_image}]" if product_image else ""

        # 캠페인가이드(자유기술)가 있으면 우선 사용
        custom_guide = campaign.get("캠페인가이드", "").strip()
        if custom_guide:
            product_name = self._display_name(campaign)
            parts = []
            if image_tag:
                parts.append(image_tag)
            parts.extend([
                "━━━━━━━━━━━━━━━━━━",
                f"📌 {product_name} 구매 가이드",
                "━━━━━━━━━━━━━━━━━━",
                "",
                custom_guide,
                "",
            ])
            buy_time = campaign.get("구매가능시간", "").strip()
            if buy_time:
                parts.append(f"⏰ 구매 가능 시간: {buy_time}")
                parts.append("")
            parts.append("✏️ 구매 완료 후 아래 양식을 입력해주세요:")
            parts.append("")
            parts.append(form_template)
            return "\n".join(parts)

        # 기존 개별 필드 기반 가이드 (하위호환)
        product_name = self._display_name(campaign)
        store_name = campaign.get("업체명", "")
        entry_method = campaign.get("유입방식", "").strip()

        parts = []
        if image_tag:
            parts.append(image_tag)
        parts.append("━━━━━━━━━━━━━━━━━━")
        parts.append(f"📌 {product_name} 구매 가이드")
        parts.append("━━━━━━━━━━━━━━━━━━")
        parts.append("")

        # 유입방식에 따라 분기
        keyword = campaign.get("키워드", "").strip()
        keyword_pos = campaign.get("키워드위치", "").strip()
        product_link = campaign.get("상품링크", "").strip()

        if "키워드" in entry_method and keyword:
            parts.append(f"🔍 키워드: {keyword}")
            if keyword_pos:
                parts.append(f"📍 위치: {keyword_pos}")
            parts.append("")
            parts.append("✅ 구매 방법:")
            step_num = 1
            parts.append(f"{step_num}. 네이버에서 '{keyword}' 검색")
            step_num += 1
            if keyword_pos:
                parts.append(f"{step_num}. {keyword_pos}에서 '{store_name}' 찾기")
                step_num += 1
            parts.append(f"{step_num}. '{store_name}'의 '{product_name}' 클릭")
            step_num += 1
        elif product_link:
            parts.append(f"🔗 구매링크: {product_link}")
            parts.append("")
            parts.append("✅ 구매 방법:")
            step_num = 1
            parts.append(f"{step_num}. 위 링크를 클릭하세요")
            step_num += 1
        else:
            parts.append("✅ 구매 방법:")
            step_num = 1
            if product_link:
                parts.append(f"{step_num}. 상품링크: {product_link}")
                step_num += 1

        # 체류시간
        dwell_time = campaign.get("체류시간", "").strip()
        if dwell_time:
            parts.append(f"{step_num}. ⏱ 상품페이지에서 {dwell_time} 이상 체류")
            step_num += 1

        # 찜/알림
        if campaign.get("상품찜필수", "").strip().upper() in ("Y", "O", "예"):
            parts.append(f"{step_num}. ❤️ 상품찜 필수")
            step_num += 1
        if campaign.get("알림받기필수", "").strip().upper() in ("Y", "O", "예"):
            parts.append(f"{step_num}. 🔔 알림받기 필수")
            step_num += 1

        # 옵션 안내
        option_mode = campaign.get("옵션지정방식", "").strip()
        option_text = campaign.get("옵션", "").strip()

        if option_selection:
            # 아이디별 선택된 옵션
            for sid in (store_ids or []):
                opt = option_selection.get(sid, {})
                if opt:
                    parts.append(f"{step_num}. 📦 {sid} 옵션: '{opt.get('name', '')}' 선택")
                    step_num += 1
        elif option_mode == "자율":
            parts.append(f"{step_num}. 옵션: 자율 선택")
            step_num += 1
        elif option_mode == "지정예정":
            parts.append(f"{step_num}. 옵션: 지정해드립니다. 미리 구매하지 마세요!")
            step_num += 1
        elif option_text:
            parts.append(f"{step_num}. 📦 옵션: {option_text}")
            step_num += 1

        # 결제방법
        pay_method = campaign.get("결제방법", "").strip()
        if pay_method and pay_method != "자율":
            parts.append(f"{step_num}. 💳 결제: {pay_method}")
            step_num += 1

        # 구매 가능 시간
        buy_time = campaign.get("구매가능시간", "").strip()
        if buy_time:
            parts.append(f"\n⏰ 구매 가능 시간: {buy_time}")

        # 배송메모
        if campaign.get("배송메모필수", "").strip().upper() in ("Y", "O", "예"):
            memo_content = campaign.get("배송메모내용", "").strip()
            parts.append(f"\n📦 배송메모: 반드시 '{memo_content}' 입력!")
            memo_link = campaign.get("배송메모안내링크", "").strip()
            if memo_link:
                parts.append(f"📎 배송메모 입력 안내: {memo_link}")

        # 주의사항
        warnings = []
        if campaign.get("광고클릭금지", "").strip().upper() in ("Y", "O", "예"):
            warnings.append("❌ 광고 절대 클릭 금지 (업체 모니터링중)")
        if campaign.get("블라인드계정금지", "").strip().upper() in ("Y", "O", "예"):
            warnings.append("❌ 블라인드 계정 사용 불가")
        if campaign.get("재구매확인", "").strip().upper() in ("Y", "O", "예"):
            warnings.append("⚠️ 재구매 여부 반드시 확인! 중복구매 불가")
        if campaign.get("배송메모필수", "").strip().upper() in ("Y", "O", "예"):
            memo_content = campaign.get("배송메모내용", "").strip()
            warnings.append(f"📦 배송메모 '{memo_content}' 빠지면 취소 후 재주문!")

        additional = campaign.get("추가안내사항", "").strip()
        if additional:
            for line in additional.split("\n"):
                line = line.strip()
                if line:
                    warnings.append(line)

        if warnings:
            parts.append("\n⚠️ 주의사항:")
            for w in warnings:
                parts.append(f"- {w}")

        # 리뷰 안내
        review_type = campaign.get("리뷰타입", "").strip()
        review_guide_content = campaign.get("리뷰가이드내용", "").strip()
        review_guide_legacy = campaign.get("리뷰가이드", "").strip()

        parts.append("")
        if review_type == "텍스트제공":
            parts.append("📝 리뷰: 텍스트 전달 예정입니다! 받으신 후 작성해주세요.")
        elif review_type == "이미지제공":
            parts.append("📝 리뷰: 리뷰 이미지 전달 예정입니다!")
        elif review_type == "포토리뷰필수":
            parts.append("📸 포토리뷰 필수! 사진 포함하여 작성해주세요.")
        elif review_guide_content:
            parts.append(f"📝 리뷰: {review_guide_content}")
        elif review_guide_legacy:
            parts.append(f"📝 리뷰: {review_guide_legacy}")
        else:
            parts.append("📝 리뷰: 자유롭게 작성해주세요!")

        # 양식 요청
        parts.append("")
        parts.append("✏️ 구매 완료 후 아래 양식을 입력해주세요:")
        parts.append("")
        parts.append(form_template)

        return "\n".join(parts)

    # ─────────── 포맷팅 ───────────

    def _format_status(self, items: dict, limit: int = 0) -> str:
        # 전체 목록 합쳐서 최신순 정렬 후 limit 적용
        all_items = []
        for item in items.get("in_progress", []):
            all_items.append(("progress", item))
        for item in items.get("completed", []):
            all_items.append(("done", item))

        total_count = len(all_items)
        show_items = all_items[:limit] if limit and limit < total_count else all_items
        hidden = total_count - len(show_items)

        text = ""
        for kind, item in show_items:
            status = item.get("상태", "")
            if kind == "progress":
                emoji = self._status_emoji(status)
                text += f"\n📦 {item.get('제품명', '')}\n"
                text += f"   아이디: {item.get('아이디', '')}\n"
                text += f"   상태: {status} {emoji}\n"
                if item.get("구매일"):
                    text += f"   구매일: {item.get('구매일')}\n"
                if item.get("리뷰기한"):
                    text += f"   리뷰기한: {item.get('리뷰기한')}\n"
                remark = item.get("비고", "")
                if remark.startswith("반려"):
                    text += f"   ⚠️ {remark}\n"
            else:
                text += f"\n📦 {item.get('제품명', '')}\n"
                text += f"   아이디: {item.get('아이디', '')}\n"
                text += f"   상태: {status} ✅\n"
                if item.get("입금금액"):
                    text += f"   입금액: {item.get('입금금액')}원\n"

        if hidden > 0:
            text += f"\n... 외 {hidden}건 더 있음"

        return text or "진행 중인 체험단이 없습니다."

    def _format_payments(self, payments: dict) -> str:
        text = ""
        if payments["paid"]:
            total_amount = sum(int(p.get("입금금액", 0) or 0) for p in payments["paid"])
            text += f"💰 입금 완료 ({len(payments['paid'])}건 / {total_amount:,}원)\n"
            for p in payments["paid"]:
                text += f"  ├── {p.get('제품명', '')} | {p.get('아이디', '')} | {p.get('입금금액', '')}원 | {p.get('입금정리', '')}\n"

        if payments["pending"]:
            text += f"\n⏳ 입금 예정 ({len(payments['pending'])}건)\n"
            for p in payments["pending"]:
                text += f"  └── {p.get('제품명', '')} | {p.get('아이디', '')} | 리뷰완료({p.get('리뷰제출일', '')})\n"

        if payments["no_review"]:
            text += f"\n📝 리뷰 미제출 ({len(payments['no_review'])}건)\n"
            for p in payments["no_review"]:
                text += f"  └── {p.get('제품명', '')} | {p.get('아이디', '')} | 기한: {p.get('리뷰기한', '')}\n"

        return text or "입금 내역이 없습니다."

    @staticmethod
    def _status_emoji(status: str) -> str:
        return {
            "신청": "⚪",
            "가이드전달": "🟡",
            "구매캡쳐대기": "🔵",
            "리뷰대기": "🟠",
            "리뷰제출": "🟢",
            "입금대기": "💰",
            "입금완료": "✅",
            "타임아웃취소": "⏰",
            "취소": "⛔",
        }.get(status, "")

    # ─────────── AI 폴백 ───────────

    def _build_ai_context(self, state: ReviewerState) -> dict:
        """AI 응답에 전달할 리뷰어 컨텍스트"""
        import models

        campaign = state.temp_data.get("campaign", {})
        items = {}
        try:
            if self.reviewers:
                items = self.reviewers.get_items(state.name, state.phone)
        except Exception:
            pass

        # 학습된 Q&A (답변 완료된 문의, 최근 30건)
        learned_qa = ""
        try:
            from modules.ai_handler import MAX_LEARNED_QA
            if models.db_manager:
                resolved = models.db_manager.get_learned_qa(limit=MAX_LEARNED_QA)
                if resolved:
                    lines = []
                    for qa in resolved:
                        lines.append(f"Q: {qa['message']}\nA: {qa['admin_reply']}")
                    learned_qa = "\n\n".join(lines)
        except Exception:
            pass

        return {
            "reviewer_name": state.name,
            "current_step": state.step,
            "campaign_name": self._display_name(campaign),
            "in_progress_count": len(items.get("in_progress", [])),
            "learned_qa": learned_qa,
        }

    def _ask_ai(self, state: ReviewerState, user_message: str):
        """AI 응답 폴백 (매칭 안 되는 자유 텍스트)"""
        fallback_buttons = [
            {"label": "📞 담당자에게 문의남기기", "value": "__inquiry__"},
        ] + list(self._menu_buttons())

        if not self.ai_handler:
            return _resp(
                "답변이 어려운 내용이에요. 담당자에게 문의를 남겨주시면 빠르게 답변드리겠습니다.",
                buttons=fallback_buttons
            )

        try:
            context = self._build_ai_context(state)
            result = self.ai_handler.get_response(user_message, context)
            if result and result.get("message"):
                # [EDIT] 태그 → 정보 수정 안내
                if result.get("edit"):
                    buttons = [
                        {"label": "✏️ 정보 수정하기", "value": "__edit__"},
                        {"label": "↩ 메뉴로", "value": "메뉴"},
                    ]
                    return _resp(result["message"], buttons=buttons)

                buttons = list(self._menu_buttons())
                if not result.get("confident"):
                    buttons.insert(0, {"label": "📞 담당자에게 문의남기기", "value": "__inquiry__"})
                if result.get("urgent"):
                    state.temp_data["_inquiry_urgent"] = True
                return _resp(result["message"], buttons=buttons)
        except Exception as e:
            logger.error(f"AI 응답 실패: {e}")

        return _resp(
            "답변이 어려운 내용이에요. 담당자에게 문의를 남겨주시면 빠르게 답변드리겠습니다.",
            buttons=fallback_buttons
        )

    # ─────────── STEP 9: 문의 모드 (AI 응답) ───────────

    def _step9_inquiry_ai(self, state: ReviewerState, message: str):
        """기타 문의: AI 응답 후 담당자 연결 버튼"""
        # 담당자 문의 버튼 클릭
        if message == "__inquiry__":
            state.step = 10
            return _resp("담당자에게 전달할 문의 내용을 입력해주세요.",
                         buttons=[{"label": "↩ 메뉴로", "value": "메뉴"}])

        # AI 응답 시도
        if self.ai_handler:
            try:
                context = self._build_ai_context(state)
                result = self.ai_handler.get_response(message, context)
                if result and result.get("message"):
                    buttons = [{"label": "📞 담당자에게 문의남기기", "value": "__inquiry__"},
                               {"label": "↩ 메뉴로", "value": "메뉴"}]
                    if result.get("urgent"):
                        state.temp_data["_inquiry_urgent"] = True
                    return _resp(result["message"], buttons=buttons)
            except Exception as e:
                logger.error(f"AI 문의 응답 실패: {e}")

        # AI 실패 시
        return _resp(
            "죄송합니다. 자동 답변이 어렵습니다.\n담당자에게 문의를 남기시겠어요?",
            buttons=[
                {"label": "📞 담당자에게 문의남기기", "value": "__inquiry__"},
                {"label": "↩ 메뉴로", "value": "메뉴"},
            ]
        )

    # ─────────── STEP 10: 문의 메시지 입력 ───────────

    def _step10_inquiry_submit(self, state: ReviewerState, message: str):
        """담당자 문의 접수"""
        import models

        is_urgent = state.temp_data.pop("_inquiry_urgent", False)

        # 최근 대화 맥락
        context_lines = ""
        try:
            history = self.chat_logger.get_history(state.reviewer_id)
            context_lines = "\n".join(
                f"[{h['sender']}] {h['message'][:100]}" for h in history[-10:]
            )
        except Exception:
            pass

        # DB 저장
        inquiry_id = 0
        if models.db_manager:
            try:
                reviewer = models.db_manager.get_reviewer(state.name, state.phone)
                reviewer_id = reviewer["id"] if reviewer else 0
                inquiry_id = models.db_manager.create_inquiry(
                    reviewer_id=reviewer_id,
                    name=state.name,
                    phone=state.phone,
                    message=message,
                    context=context_lines,
                    is_urgent=is_urgent,
                )
            except Exception as e:
                logger.error(f"문의 저장 실패: {e}")

        # 활성 담당자에게 카톡 알림 (긴급/일반 모두)
        if models.kakao_notifier:
            try:
                managers = []
                if models.db_manager:
                    managers = models.db_manager.get_active_managers()
                if not managers:
                    managers = [{"name": "오동열", "phone": "010-7210-0210"}]
                for mgr in managers:
                    if is_urgent:
                        models.kakao_notifier.notify_admin_urgent_inquiry(
                            admin_name=mgr["name"], admin_phone=mgr["phone"],
                            reviewer_name=state.name, reviewer_phone=state.phone,
                            message=message,
                        )
                    else:
                        models.kakao_notifier.notify_admin_inquiry(
                            admin_name=mgr["name"], admin_phone=mgr["phone"],
                            reviewer_name=state.name, reviewer_phone=state.phone,
                            message=message,
                        )
            except Exception as e:
                logger.warning(f"문의 알림 실패: {e}")

        state.step = 0
        state.temp_data = {}
        return _resp(
            "✅ 문의가 접수되었습니다!\n담당자 확인 후 빠른 시일 내 답변드리겠습니다.",
            buttons=self._menu_buttons()
        )

    # ─────────── 정보 수정 (STEP 11~13) ───────────

    _EDITABLE_FIELDS = [
        {"label": "계좌정보 (은행/계좌/예금주)", "value": "edit_account"},
        {"label": "아이디", "value": "edit_아이디"},
        {"label": "수취인명", "value": "edit_수취인명"},
        {"label": "연락처", "value": "edit_연락처"},
        {"label": "주소", "value": "edit_주소"},
        {"label": "주문번호", "value": "edit_주문번호"},
    ]

    def _enter_edit_mode(self, state: ReviewerState):
        """정보 수정 모드 진입 → 진행 건 목록 표시"""
        import models
        items = self.reviewers.get_items(state.name, state.phone)
        in_progress = items.get("in_progress", [])

        if not in_progress:
            return _resp("현재 진행 중인 건이 없습니다.", buttons=self._menu_buttons())

        if len(in_progress) == 1:
            # 1건이면 바로 항목 선택으로
            item = in_progress[0]
            state.step = 12
            state.temp_data["_edit_progress_id"] = item.get("_row_idx") or item.get("id")
            state.temp_data["_edit_product"] = item.get("제품명", "")
            return _resp(
                f"✏️ [{item.get('제품명', '')}] 수정할 항목을 선택해주세요.",
                buttons=self._EDITABLE_FIELDS + [{"label": "↩ 메뉴로", "value": "메뉴"}]
            )

        # 여러 건이면 선택
        state.step = 11
        buttons = []
        for item in in_progress:
            pid = item.get("_row_idx") or item.get("id")
            label = f"{item.get('제품명', '')} ({item.get('아이디', '')})"
            buttons.append({"label": label, "value": f"editpick_{pid}"})
        buttons.append({"label": "↩ 메뉴로", "value": "메뉴"})
        return _resp("수정할 건을 선택해주세요.", buttons=buttons)

    def _step11_edit_select(self, state: ReviewerState, message: str):
        """STEP 11: 수정할 진행 건 선택"""
        if message.startswith("editpick_"):
            try:
                pid = int(message.replace("editpick_", ""))
            except ValueError:
                return _resp("잘못된 선택입니다.", buttons=[{"label": "↩ 메뉴로", "value": "메뉴"}])

            import models
            row = models.db_manager.get_row_dict(pid) if models.db_manager else {}
            if not row:
                return _resp("해당 건을 찾을 수 없습니다.", buttons=self._menu_buttons())

            state.step = 12
            state.temp_data["_edit_progress_id"] = pid
            state.temp_data["_edit_product"] = row.get("제품명", "")
            return _resp(
                f"✏️ [{row.get('제품명', '')}] 수정할 항목을 선택해주세요.",
                buttons=self._EDITABLE_FIELDS + [{"label": "↩ 메뉴로", "value": "메뉴"}]
            )

        return _resp("수정할 건을 선택해주세요.", buttons=[{"label": "↩ 메뉴로", "value": "메뉴"}])

    def _step12_edit_field(self, state: ReviewerState, message: str):
        """STEP 12: 수정할 항목 선택"""
        import models
        pid = state.temp_data.get("_edit_progress_id")
        row = models.db_manager.get_row_dict(pid) if models.db_manager and pid else {}

        if message == "edit_account":
            state.step = 13
            state.temp_data["_edit_fields"] = ["은행", "계좌", "예금주"]
            current = f"현재: {row.get('은행', '-')} / {row.get('계좌', '-')} / {row.get('예금주', '-')}"
            return _resp(
                f"{current}\n\n새 계좌정보를 입력해주세요.\n예시: 국민은행 1234567890 홍길동",
                buttons=[{"label": "↩ 이전", "value": "__back_edit__"}]
            )

        field_map = {
            "edit_아이디": "아이디", "edit_수취인명": "수취인명",
            "edit_연락처": "연락처", "edit_주소": "주소", "edit_주문번호": "주문번호",
        }
        field = field_map.get(message)
        if field:
            state.step = 13
            state.temp_data["_edit_fields"] = [field]
            current = row.get(field, "-")
            return _resp(
                f"현재 {field}: {current}\n\n새 {field}을(를) 입력해주세요.",
                buttons=[{"label": "↩ 이전", "value": "__back_edit__"}]
            )

        if message == "__back_edit__":
            state.step = 12
            return _resp(
                f"✏️ [{state.temp_data.get('_edit_product', '')}] 수정할 항목을 선택해주세요.",
                buttons=self._EDITABLE_FIELDS + [{"label": "↩ 메뉴로", "value": "메뉴"}]
            )

        return _resp("수정할 항목을 선택해주세요.",
                     buttons=self._EDITABLE_FIELDS + [{"label": "↩ 메뉴로", "value": "메뉴"}])

    def _step13_edit_value(self, state: ReviewerState, message: str):
        """STEP 13: 새 값 입력 → DB 업데이트"""
        import models

        if message == "__back_edit__":
            state.step = 12
            return _resp(
                f"✏️ [{state.temp_data.get('_edit_product', '')}] 수정할 항목을 선택해주세요.",
                buttons=self._EDITABLE_FIELDS + [{"label": "↩ 메뉴로", "value": "메뉴"}]
            )

        pid = state.temp_data.get("_edit_progress_id")
        fields = state.temp_data.get("_edit_fields", [])

        if not pid or not fields or not models.db_manager:
            state.step = 0
            return _resp("오류가 발생했습니다.", buttons=self._menu_buttons())

        try:
            if fields == ["은행", "계좌", "예금주"]:
                # 계좌정보: "은행 계좌번호 예금주" 파싱
                parts = message.strip().split()
                if len(parts) < 3:
                    return _resp(
                        "형식을 확인해주세요.\n예시: 국민은행 1234567890 홍길동",
                        buttons=[{"label": "↩ 이전", "value": "__back_edit__"}]
                    )
                models.db_manager.update_progress_field(pid, "은행", parts[0])
                models.db_manager.update_progress_field(pid, "계좌", parts[1])
                models.db_manager.update_progress_field(pid, "예금주", " ".join(parts[2:]))
                changed = f"은행: {parts[0]}, 계좌: {parts[1]}, 예금주: {' '.join(parts[2:])}"
            else:
                field = fields[0]
                models.db_manager.update_progress_field(pid, field, message.strip())
                changed = f"{field}: {message.strip()}"

            logger.info(f"정보 수정: progress_id={pid}, {changed} (by {state.name})")

        except Exception as e:
            logger.error(f"정보 수정 실패: {e}")
            state.step = 0
            return _resp("수정 중 오류가 발생했습니다.", buttons=self._menu_buttons())

        state.step = 0
        state.temp_data = {}
        return _resp(
            f"✅ 수정 완료!\n{changed}",
            buttons=self._menu_buttons()
        )

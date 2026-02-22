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
        if state.step == 0:
            return _resp(
                tpl.WELCOME_BACK.format(name=name),
                buttons=self._menu_buttons()
            )
        # 진행 중인 세션이 있으면 이어하기/새로 시작 선택
        campaign = state.temp_data.get("campaign", {})
        product = campaign.get("상품명", "")
        header = f"📌 진행 중인 신청이 있습니다.\n📦 {product}" if product else "📌 진행 중인 신청이 있습니다."
        return _resp(
            f"{header}\n\n이어서 진행하시겠습니까?",
            buttons=[
                {"label": "이어하기", "value": "__resume__"},
                {"label": "새로 시작", "value": "__cancel__", "style": "danger"},
            ]
        )

    def _menu_buttons(self):
        return [
            {"label": "캠페인 목록", "value": "1"},
            {"label": "내 진행현황", "value": "2"},
            {"label": "사진 제출", "value": "3"},
            {"label": "입금 확인", "value": "4"},
        ]

    def _back_button(self, value="__back__"):
        return {"label": "↩ 이전 단계", "value": value, "style": "secondary"}

    def _cancel_button(self):
        return {"label": "취소", "value": "__cancel__", "style": "danger"}

    def _build_resume_message(self, state: ReviewerState):
        """진행 중인 세션 복귀 안내"""
        campaign = state.temp_data.get("campaign", {})
        product = campaign.get("상품명", "")
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

        if step == 0:
            return self._step0_menu(state, msg)
        elif step == 1:
            return self._step1_campaign(state, msg)
        elif step == 2:
            return self._step2_account_count(state, msg)
        elif step == 3:
            return self._step3_collect_ids(state, msg)
        elif step == 4:
            return self._step4_guide_and_form(state, msg)
        elif step == 5:
            return self._step5_form(state, msg)
        elif step == 6:
            return self._step6_purchase(state, msg)
        elif step == 7:
            return self._step7_review(state, msg)
        elif step == 8:
            return self._step8_done(state, msg)
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
                    product_name=campaign.get("상품명", ""),
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
                self.reviewers.sheets.cancel_by_timeout(
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
            state.step = 1
            cards = self.campaigns.build_campaign_cards(state.name, state.phone)
            if not cards:
                state.step = 0
                return _resp(tpl.NO_CAMPAIGNS, buttons=self._menu_buttons())
            return _resp("현재 모집 중인 체험단입니다:", cards=cards)

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
            return _resp("궁금한 점을 말씀해주세요! 담당자가 확인 후 답변드리겠습니다.",
                         buttons=self._menu_buttons())

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
        if not campaign.get("_buy_time_active", True):
            buy_time = campaign.get("구매가능시간", "")
            cards = self.campaigns.build_campaign_cards(state.name, state.phone)
            return _resp(
                f"'{campaign.get('상품명', '')}' 캠페인은 구매 가능 시간이 아닙니다.\n⏰ 진행시간: {buy_time}",
                cards=cards
            )

        # 금일 모집목표 도달 체크
        if self.campaigns.is_daily_full(campaign):
            cards = self.campaigns.build_campaign_cards(state.name, state.phone)
            return _resp(
                f"'{campaign.get('상품명', '')}' 캠페인은 오늘 모집이 마감되었습니다.\n내일 다시 신청해주세요!",
                cards=cards
            )

        state.selected_campaign_id = campaign.get("캠페인ID", str(choice))
        state.temp_data["campaign"] = campaign
        state.temp_data["store_ids"] = []
        state.step = 2

        return _resp(
            tpl.ASK_ACCOUNT_COUNT.format(
                product_name=campaign.get("상품명", ""),
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
        """다중 선택용 이전 아이디 데이터 (2개 이상 선택 시) - API 호출 최소화"""
        try:
            if not self.reviewers or not self.reviewers.sheets:
                return None
            sheets = self.reviewers.sheets

            # 시트 1회 읽기로 모든 데이터 확보
            ws = sheets._get_ws()
            headers = sheets._get_headers(ws)
            all_rows = ws.get_all_values()

            # 이 리뷰어의 사용 아이디 수집
            used_ids = set()
            for row in all_rows[1:]:
                if sheets._match_reviewer(row, headers, state.name, state.phone):
                    sid_col = sheets._find_col(headers, "아이디")
                    if sid_col >= 0 and len(row) > sid_col:
                        sid = row[sid_col].strip()
                        if sid:
                            used_ids.add(sid)
            if not used_ids:
                return None

            # 이 캠페인에서 이미 진행중인 아이디 (메모리에서 체크)
            campaign = state.temp_data.get("campaign", {})
            allow_dup = campaign.get("중복허용", "").strip().upper() in ("Y", "O", "예", "허용")
            active_ids = set()

            if not allow_dup and campaign_id:
                cid_col = sheets._find_col(headers, "캠페인ID")
                sid_col = sheets._find_col(headers, "아이디")
                status_col = sheets._find_col(headers, "상태")
                if cid_col >= 0 and sid_col >= 0:
                    for row in all_rows[1:]:
                        if len(row) <= max(cid_col, sid_col):
                            continue
                        if row[cid_col] != campaign_id:
                            continue
                        sid = row[sid_col].strip()
                        if sid not in used_ids:
                            continue
                        status = row[status_col] if status_col >= 0 and len(row) > status_col else ""
                        if status not in sheets._DUP_IGNORE_STATUSES:
                            active_ids.add(sid)

            items = []
            for sid in sorted(used_ids)[:8]:
                disabled = sid in active_ids
                items.append({
                    "id": sid,
                    "disabled": disabled,
                    "reason": "진행중" if disabled else "",
                })

            return {
                "max_select": max_select,
                "items": items,
            }
        except Exception:
            return None

    def _prev_id_buttons(self, state: ReviewerState):
        """이전에 사용한 아이디 버튼 목록"""
        try:
            if not self.reviewers or not self.reviewers.sheets:
                return []
            all_items = self.reviewers.sheets.search_by_name_phone(state.name, state.phone)
            used_ids = set()
            for item in all_items:
                sid = item.get("아이디", "").strip()
                if sid:
                    used_ids.add(sid)
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

        # 시트에 등록
        for sid in ids:
            self.reviewers.register(state.name, state.phone, campaign, sid)
        for sid in ids:
            self._update_status_by_id(state.name, state.phone, campaign_id, sid, "가이드전달")

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
        if len(ids) > 1:
            confirm += f"\n\n📋 {len(ids)}개 계정 각각 양식을 제출해주세요."

        guide = self._build_purchase_guide(campaign, state.name, state.phone, ids)
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
                f"✅ 옵션 선택 완료:\n{summary}\n\n"
                f"📋 {len(ids)}개 계정 각각 양식을 제출해주세요.\n\n{guide}",
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
            required = ["수취인명", "연락처", "은행", "계좌", "예금주"]
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
            required = ["수취인명", "연락처", "은행", "계좌", "예금주"]
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
                product_name=campaign.get("상품명", ""),
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
            if not self.reviewers or not self.reviewers.sheets:
                return
            sheets = self.reviewers.sheets
            ws = sheets._get_ws()
            headers = sheets._get_headers(ws)
            all_rows = ws.get_all_values()

            cid_col = sheets._find_col(headers, "캠페인ID")
            sid_col = sheets._find_col(headers, "아이디")
            status_col = sheets._find_col(headers, "상태")

            for i, row in enumerate(all_rows[1:], start=2):
                if cid_col < 0 or sid_col < 0 or len(row) <= max(cid_col, sid_col):
                    continue
                if row[cid_col] != campaign_id or row[sid_col] != store_id:
                    continue
                if not sheets._match_reviewer(row, headers, name, phone):
                    continue
                ws.update_cell(i, status_col + 1, new_status)
                break
        except Exception as e:
            logger.error(f"상태 업데이트 에러: {e}")

    # ─────────── 구매 가이드 빌더 (조건부 생성) ───────────

    def _build_form_template(self, campaign: dict, name: str, phone: str,
                              store_ids: list = None) -> str:
        prev_info = {}
        try:
            if self.reviewers and self.reviewers.sheets:
                prev_info = self.reviewers.sheets.get_user_prev_info(name, phone)
        except Exception as e:
            logger.error(f"기존 정보 조회 에러: {e}")

        lines = []
        if store_ids and len(store_ids) > 1:
            lines.append("아이디: ")
        elif store_ids and len(store_ids) == 1:
            lines.append(f"아이디: {store_ids[0]}")

        guide_amount = campaign.get("결제금액", "") if campaign else ""

        lines += [
            "수취인명: ",
            "연락처: ",
            f"결제금액: {guide_amount}",
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

        # 캠페인가이드(자유기술)가 있으면 우선 사용
        custom_guide = campaign.get("캠페인가이드", "").strip()
        if custom_guide:
            product_name = campaign.get("상품명", "")
            parts = [
                "━━━━━━━━━━━━━━━━━━",
                f"📌 {product_name} 구매 가이드",
                "━━━━━━━━━━━━━━━━━━",
                "",
                custom_guide,
                "",
            ]
            buy_time = campaign.get("구매가능시간", "").strip()
            if buy_time:
                parts.append(f"⏰ 구매 가능 시간: {buy_time}")
                parts.append("")
            parts.append("✏️ 구매 완료 후 아래 양식을 입력해주세요:")
            parts.append("")
            parts.append(form_template)
            return "\n".join(parts)

        # 기존 개별 필드 기반 가이드 (하위호환)
        product_name = campaign.get("상품명", "")
        store_name = campaign.get("업체명", "")
        entry_method = campaign.get("유입방식", "").strip()

        parts = []
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
            "구매내역제출": "🔵",
            "리뷰제출": "🟢",
            "입금대기": "💰",
            "입금완료": "✅",
            "타임아웃취소": "⏰",
            "취소": "⛔",
        }.get(status, "")

    # ─────────── AI 폴백 ───────────

    def _build_ai_context(self, state: ReviewerState) -> dict:
        """AI 응답에 전달할 리뷰어 컨텍스트"""
        campaign = state.temp_data.get("campaign", {})
        items = {}
        try:
            if self.reviewers:
                items = self.reviewers.get_items(state.name, state.phone)
        except Exception:
            pass

        return {
            "reviewer_name": state.name,
            "current_step": state.step,
            "campaign_name": campaign.get("상품명", ""),
            "in_progress_count": len(items.get("in_progress", [])),
        }

    def _ask_ai(self, state: ReviewerState, user_message: str):
        """AI 응답 폴백 (매칭 안 되는 자유 텍스트)"""
        if not self.ai_handler:
            return _resp(tpl.UNKNOWN_INPUT, buttons=self._menu_buttons())

        try:
            context = self._build_ai_context(state)
            ai_reply = self.ai_handler.get_response(user_message, context)
            if ai_reply:
                return _resp(ai_reply, buttons=self._menu_buttons())
        except Exception as e:
            logger.error(f"AI 응답 실패: {e}")

        return _resp(tpl.UNKNOWN_INPUT, buttons=self._menu_buttons())

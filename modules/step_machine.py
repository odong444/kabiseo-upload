"""
step_machine.py - 채팅 대화 로직

STEP 0: 메뉴 선택 (본인확인)
STEP 9: 기타 문의 (AI 응답)
STEP 10: 담당자 문의 접수
STEP 11~13: 정보 수정

캠페인 신청(STEP 1~8)은 웹으로 전환되어 제거됨.
"""

import re
import json
import logging
from modules.state_store import StateStore, ReviewerState
from modules.form_parser import parse_menu_choice
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
            {"label": "배송사고 문의", "value": "__q_shipping__"},
            {"label": "입금 문의", "value": "__q_payment__"},
            {"label": "진행 문의", "value": "__q_progress__"},
            {"label": "정보수정 문의", "value": "__q_edit__"},
            {"label": "기타 문의", "value": "__q_etc__"},
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

        if state.step in (1, 2, 3):
            # 캠페인 신청(step 1~3)은 웹으로 전환됨 → 메뉴로 복귀
            state.step = 0
            state.temp_data = {}
            campaigns_url = f"{self.web_url}/campaigns" if self.web_url else "/campaigns"
            return _resp(
                f"캠페인 신청은 웹페이지에서 진행해주세요!\n🔗 {campaigns_url}",
                buttons=self._menu_buttons()
            )

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

        # 퀵메뉴 문의 카테고리
        if msg == "__q_shipping__":
            state.step = 9
            state.temp_data["inquiry_category"] = "배송사고"
            return _resp(
                "📦 배송사고 문의입니다.\n배송 관련 궁금한 점을 입력해주세요!\n\n예) 배송이 안 와요 / 배송 지연 / 파손",
                buttons=[{"label": "↩ 메뉴로", "value": "메뉴"}])
        if msg == "__q_payment__":
            state.step = 9
            state.temp_data["inquiry_category"] = "입금"
            return _resp(
                "💰 입금 문의입니다.\n입금 관련 궁금한 점을 입력해주세요!\n\n예) 입금이 안 됐어요 / 입금 언제 되나요 / 입금액이 다릅니다",
                buttons=[{"label": "↩ 메뉴로", "value": "메뉴"}])
        if msg == "__q_progress__":
            state.step = 9
            state.temp_data["inquiry_category"] = "진행"
            return _resp(
                "📋 진행 문의입니다.\n진행 관련 궁금한 점을 입력해주세요!\n\n예) 상품을 못 찾겠어요 / 타임아웃 됐어요 / 리뷰 기한이 언제인가요",
                buttons=[{"label": "↩ 메뉴로", "value": "메뉴"}])
        if msg == "__q_edit__":
            return self._enter_edit_mode(state)
        if msg == "__q_etc__":
            state.step = 9
            state.temp_data["inquiry_category"] = "기타"
            return _resp(
                "💬 기타 문의입니다.\n궁금한 점을 자유롭게 입력해주세요!\nAI가 답변드리고, 필요하면 담당자에게 연결해드릴게요.",
                buttons=[{"label": "↩ 메뉴로", "value": "메뉴"}])

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

        elif step in (2, 3, 4, 5):
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

    # ─────────── 시트 상태 업데이트 헬퍼 ───────────

    def _update_status_by_id(self, name, phone, campaign_id, store_id, new_status):
        try:
            if not self.reviewers or not self.reviewers.db:
                return
            self.reviewers.db.update_status_by_id(name, phone, campaign_id, store_id, new_status)
        except Exception as e:
            logger.error(f"상태 업데이트 에러: {e}")

    # ─────────── 양식 템플릿 빌더 ───────────

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

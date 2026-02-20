"""
step_machine.py - 핵심 STEP 0~7 대화 로직

STEP 0: 메뉴 선택
STEP 1: 캠페인 선택
STEP 2: 본인 확인 (이름+연락처)
STEP 3: 가이드 전달 + 아이디 입력 요청
STEP 4: 양식 접수 (아이디 파싱)
STEP 5: 구매캡쳐 안내
STEP 6: 리뷰캡쳐 안내
STEP 7: 완료
"""

import logging
from modules.state_store import StateStore, ReviewerState
from modules.form_parser import parse_menu_choice, parse_campaign_choice, parse_form
from modules.campaign_manager import CampaignManager
from modules.reviewer_manager import ReviewerManager
from modules.chat_logger import ChatLogger
from modules import response_templates as tpl
from modules.utils import today_str

logger = logging.getLogger(__name__)


class StepMachine:
    """대화 STEP 처리 엔진"""

    def __init__(self, state_store: StateStore, campaign_mgr: CampaignManager,
                 reviewer_mgr: ReviewerManager, chat_logger: ChatLogger,
                 web_url: str = ""):
        self.states = state_store
        self.campaigns = campaign_mgr
        self.reviewers = reviewer_mgr
        self.chat_logger = chat_logger
        self.web_url = web_url

    def process_message(self, name: str, phone: str, message: str) -> str:
        """메시지 처리 → 응답 반환"""
        state = self.states.get(name, phone)

        # 대화 로깅
        self.chat_logger.log(state.reviewer_id, "user", message)

        try:
            response = self._dispatch(state, message)
        except Exception as e:
            logger.error(f"StepMachine 에러: {e}", exc_info=True)
            response = tpl.ERROR_OCCURRED

        # 응답 로깅
        self.chat_logger.log(state.reviewer_id, "bot", response)
        return response

    def get_welcome(self, name: str, phone: str) -> str:
        """접속 시 환영 메시지"""
        state = self.states.get(name, phone)
        if state.step == 0:
            return tpl.WELCOME_BACK.format(name=name)
        return ""

    def _dispatch(self, state: ReviewerState, message: str) -> str:
        """현재 STEP에 따라 처리 분기"""
        step = state.step

        # "메뉴", "처음", "돌아가기" → STEP 0으로 리셋
        if message.strip() in ("메뉴", "처음", "돌아가기", "홈"):
            state.step = 0
            return tpl.WELCOME_BACK.format(name=state.name)

        if step == 0:
            return self._step0_menu(state, message)
        elif step == 1:
            return self._step1_campaign(state, message)
        elif step == 2:
            return self._step2_identity(state, message)
        elif step == 3:
            return self._step3_guide(state, message)
        elif step == 4:
            return self._step4_form(state, message)
        elif step == 5:
            return self._step5_purchase(state, message)
        elif step == 6:
            return self._step6_review(state, message)
        elif step == 7:
            return self._step7_done(state, message)
        else:
            state.step = 0
            return tpl.WELCOME_BACK.format(name=state.name)

    def _step0_menu(self, state: ReviewerState, message: str) -> str:
        """STEP 0: 메뉴 선택"""
        choice = parse_menu_choice(message)

        if choice == 1:
            # 체험단 신청 → 캠페인 목록
            state.step = 1
            return self.campaigns.build_campaign_list_text()

        elif choice == 2:
            # 진행 상황 → 바로 조회
            items = self.reviewers.get_items(state.name, state.phone)
            if not items["in_progress"] and not items["completed"]:
                return "진행 중인 체험단이 없습니다. 체험단을 신청해보세요!"
            return self._format_status(items)

        elif choice == 3:
            # 사진 제출 안내
            upload_url = f"{self.web_url}/upload" if self.web_url else "/upload"
            return f"📸 사진 제출은 아래 링크에서 가능합니다:\n🔗 {upload_url}\n\n또는 하단 '사진제출' 메뉴를 이용해주세요."

        elif choice == 4:
            # 입금 현황
            payments = self.reviewers.get_payments(state.name, state.phone)
            return self._format_payments(payments)

        elif choice == 5:
            return "궁금한 점을 말씀해주세요! 담당자가 확인 후 답변드리겠습니다."

        return tpl.UNKNOWN_INPUT

    def _step1_campaign(self, state: ReviewerState, message: str) -> str:
        """STEP 1: 캠페인 번호 선택"""
        choice = parse_campaign_choice(message)
        if choice is None:
            return "캠페인 번호를 입력해주세요. (숫자만 입력)"

        campaign = self.campaigns.get_campaign_by_index(choice)
        if not campaign:
            return "해당 번호의 캠페인이 없습니다. 다시 선택해주세요."

        state.selected_campaign_id = campaign.get("캠페인ID", str(choice))
        state.temp_data["campaign"] = campaign
        state.step = 3  # 본인확인 skip (웹에서 이미 이름+연락처 있음)

        return tpl.GUIDE_MESSAGE.format(
            product_name=campaign.get("상품명", ""),
            store_name=campaign.get("업체명", ""),
        )

    def _step2_identity(self, state: ReviewerState, message: str) -> str:
        """STEP 2: 본인 확인 (웹에서는 보통 skip)"""
        state.step = 3
        return tpl.IDENTITY_CONFIRMED.format(name=state.name)

    def _step3_guide(self, state: ReviewerState, message: str) -> str:
        """STEP 3: 아이디 입력 대기"""
        return self._step4_form(state, message)

    def _step4_form(self, state: ReviewerState, message: str) -> str:
        """STEP 4: 양식 접수"""
        parsed = parse_form(message)
        store_id = parsed.get("아이디", "")

        if not store_id:
            # 메시지 자체를 아이디로 시도 (단일 단어인 경우)
            stripped = message.strip()
            if stripped and " " not in stripped and len(stripped) < 30:
                store_id = stripped
            else:
                return tpl.FORM_PARSE_FAIL

        campaign = state.temp_data.get("campaign", {})
        if not campaign:
            state.step = 0
            return "캠페인 정보가 없습니다. 처음부터 다시 진행해주세요.\n\n" + tpl.WELCOME_BACK.format(name=state.name)

        # 시트에 등록
        self.reviewers.register(
            state.name, state.phone, campaign, store_id
        )

        state.step = 5
        state.temp_data["store_id"] = store_id

        upload_url = f"{self.web_url}/upload" if self.web_url else "/upload"
        return tpl.FORM_RECEIVED.format(
            product_name=campaign.get("상품명", ""),
            store_id=store_id,
            upload_url=upload_url,
        )

    def _step5_purchase(self, state: ReviewerState, message: str) -> str:
        """STEP 5: 구매캡쳐 대기"""
        choice = parse_menu_choice(message)
        if choice:
            state.step = 0
            return self._step0_menu(state, message)

        upload_url = f"{self.web_url}/upload" if self.web_url else "/upload"
        return tpl.PURCHASE_CAPTURE_REMIND.format(upload_url=upload_url)

    def _step6_review(self, state: ReviewerState, message: str) -> str:
        """STEP 6: 리뷰캡쳐 대기"""
        choice = parse_menu_choice(message)
        if choice:
            state.step = 0
            return self._step0_menu(state, message)

        upload_url = f"{self.web_url}/upload" if self.web_url else "/upload"
        return tpl.REVIEW_CAPTURE_REMIND.format(
            upload_url=upload_url,
            deadline=state.temp_data.get("deadline", "확인 필요"),
        )

    def _step7_done(self, state: ReviewerState, message: str) -> str:
        """STEP 7: 완료 상태"""
        state.step = 0
        return tpl.ALL_DONE + "\n\n" + tpl.WELCOME_BACK.format(name=state.name)

    # ──────────── 포맷팅 ────────────

    def _format_status(self, items: dict) -> str:
        text = ""
        if items["in_progress"]:
            text += "📋 진행중\n"
            for item in items["in_progress"]:
                status = item.get("상태", "")
                emoji = self._status_emoji(status)
                text += f"\n📦 {item.get('제품명', '')}\n"
                text += f"   아이디: {item.get('아이디', '')}\n"
                text += f"   상태: {status} {emoji}\n"
                if item.get("구매일"):
                    text += f"   구매일: {item.get('구매일')}\n"
                if item.get("리뷰기한"):
                    text += f"   리뷰기한: {item.get('리뷰기한')}\n"

        if items["completed"]:
            text += "\n✅ 완료\n"
            for item in items["completed"]:
                text += f"\n📦 {item.get('제품명', '')}\n"
                text += f"   아이디: {item.get('아이디', '')}\n"
                text += f"   상태: {item.get('상태', '')} ✅\n"
                if item.get("입금금액"):
                    text += f"   입금액: {item.get('입금금액')}원\n"

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
            "가이드전달": "🟡",
            "양식접수": "🔵",
            "리뷰대기": "🟠",
            "리뷰완료": "🟢",
            "정산완료": "✅",
            "취소": "⛔",
        }.get(status, "")

"""
step_machine.py - 핵심 STEP 0~8 대화 로직

STEP 0: 메뉴 선택
STEP 1: 캠페인 선택 (기존 진행 아이디 표시)
STEP 2: 몇 개 계정 진행?
STEP 3: 아이디 수집 (하나씩, 중복체크)
STEP 4: 구매 가이드 전달 + 양식 요청
STEP 5: 양식 접수 (수취인명, 연락처, 은행, 계좌, 예금주, 주소, 닉네임, 결제금액)
STEP 6: 구매캡쳐 대기
STEP 7: 리뷰캡쳐 대기
STEP 8: 완료 (입금대기)
"""

import logging
from modules.state_store import StateStore, ReviewerState
from modules.form_parser import parse_menu_choice, parse_campaign_choice, parse_full_form
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

        self.chat_logger.log(state.reviewer_id, "user", message)

        try:
            response = self._dispatch(state, message)
        except Exception as e:
            logger.error(f"StepMachine 에러: {e}", exc_info=True)
            response = tpl.ERROR_OCCURRED

        self.chat_logger.log(state.reviewer_id, "bot", response)
        return response

    def get_welcome(self, name: str, phone: str) -> str:
        """접속 시 환영 메시지"""
        state = self.states.get(name, phone)
        if state.step == 0:
            return tpl.WELCOME_BACK.format(name=name)
        return ""

    def _dispatch(self, state: ReviewerState, message: str) -> str:
        step = state.step

        if message.strip() in ("메뉴", "처음", "돌아가기", "홈"):
            state.step = 0
            state.temp_data = {}
            return tpl.WELCOME_BACK.format(name=state.name)

        if step == 0:
            return self._step0_menu(state, message)
        elif step == 1:
            return self._step1_campaign(state, message)
        elif step == 2:
            return self._step2_account_count(state, message)
        elif step == 3:
            return self._step3_collect_ids(state, message)
        elif step == 4:
            return self._step4_guide_and_form(state, message)
        elif step == 5:
            return self._step5_form(state, message)
        elif step == 6:
            return self._step6_purchase(state, message)
        elif step == 7:
            return self._step7_review(state, message)
        elif step == 8:
            return self._step8_done(state, message)
        else:
            state.step = 0
            return tpl.WELCOME_BACK.format(name=state.name)

    # ─────────── STEP 0: 메뉴 ───────────

    def _step0_menu(self, state: ReviewerState, message: str) -> str:
        choice = parse_menu_choice(message)

        if choice == 1:
            state.step = 1
            return self.campaigns.build_campaign_list_text(state.name, state.phone)

        elif choice == 2:
            items = self.reviewers.get_items(state.name, state.phone)
            if not items["in_progress"] and not items["completed"]:
                return "진행 중인 체험단이 없습니다. 체험단을 신청해보세요!"
            return self._format_status(items)

        elif choice == 3:
            upload_url = f"{self.web_url}/upload" if self.web_url else "/upload"
            return f"📸 사진 제출은 아래 링크에서 가능합니다:\n🔗 {upload_url}\n\n또는 ☰ 메뉴 → 사진제출"

        elif choice == 4:
            payments = self.reviewers.get_payments(state.name, state.phone)
            return self._format_payments(payments)

        elif choice == 5:
            return "궁금한 점을 말씀해주세요! 담당자가 확인 후 답변드리겠습니다."

        return tpl.UNKNOWN_INPUT

    # ─────────── STEP 1: 캠페인 선택 ───────────

    def _step1_campaign(self, state: ReviewerState, message: str) -> str:
        choice = parse_campaign_choice(message)
        if choice is None:
            return "캠페인 번호를 입력해주세요. (숫자만 입력)"

        campaign = self.campaigns.get_campaign_by_index(choice)
        if not campaign:
            return "해당 번호의 캠페인이 없습니다. 다시 선택해주세요."

        state.selected_campaign_id = campaign.get("캠페인ID", str(choice))
        state.temp_data["campaign"] = campaign
        state.temp_data["store_ids"] = []
        state.step = 2

        return tpl.ASK_ACCOUNT_COUNT.format(
            product_name=campaign.get("상품명", ""),
            store_name=campaign.get("업체명", ""),
        )

    # ─────────── STEP 2: 계정 수 ───────────

    def _step2_account_count(self, state: ReviewerState, message: str) -> str:
        text = message.strip()
        try:
            count = int(text)
        except ValueError:
            return "숫자를 입력해주세요. (예: 1, 2, 3)"

        if count < 1 or count > 10:
            return "1~10 사이의 숫자를 입력해주세요."

        state.temp_data["account_count"] = count
        state.temp_data["store_ids"] = []
        state.step = 3

        if count == 1:
            return tpl.ASK_STORE_ID_SINGLE
        else:
            return tpl.ASK_STORE_ID.format(n=1, current=1, total=count)

    # ─────────── STEP 3: 아이디 수집 ───────────

    def _step3_collect_ids(self, state: ReviewerState, message: str) -> str:
        store_id = message.strip()
        if not store_id or len(store_id) > 30 or "\n" in store_id:
            return "아이디를 정확히 입력해주세요. (한 줄에 하나)"

        campaign = state.temp_data.get("campaign", {})
        campaign_id = campaign.get("캠페인ID", "")
        account_count = state.temp_data.get("account_count", 1)
        collected = state.temp_data.get("store_ids", [])

        # 이미 이번에 입력한 아이디 중복 체크
        if store_id in collected:
            return f"⚠️ '{store_id}'는 이미 입력한 아이디입니다. 다른 아이디를 입력해주세요."

        # 시트 중복 체크 (캠페인별 중복허용 설정 확인)
        allow_dup = campaign.get("중복허용", "").strip().upper() in ("Y", "O", "예", "허용")
        if not allow_dup:
            is_dup = self.reviewers.check_duplicate(campaign_id, store_id)
            if is_dup:
                return tpl.DUPLICATE_FOUND.format(store_id=store_id)

        # 아이디 저장
        collected.append(store_id)
        state.temp_data["store_ids"] = collected

        # 아직 더 받아야 하면
        if len(collected) < account_count:
            next_n = len(collected) + 1
            confirm = tpl.ID_CONFIRMED.format(store_id=store_id)
            ask_next = tpl.ASK_STORE_ID.format(n=next_n, current=next_n, total=account_count)
            return f"{confirm}\n\n{ask_next}"

        # 모든 아이디 수집 완료 → 시트에 "신청" 상태로 등록 + 구매 가이드
        state.step = 4
        confirm = tpl.ID_CONFIRMED.format(store_id=store_id)

        id_summary = ", ".join(collected)
        if account_count > 1:
            confirm += f"\n\n🆔 전체 아이디: {id_summary}"

        # 시트에 각 아이디별 "신청" 상태로 미리 등록
        for sid in collected:
            self.reviewers.register(
                state.name, state.phone, campaign, sid
            )

        # 가이드 전달 → 상태 "가이드전달"로 업데이트
        for sid in collected:
            self._update_status_by_id(state.name, state.phone, campaign_id, sid, "가이드전달")

        # 구매 가이드 자동 전달
        guide = self._build_purchase_guide(campaign)
        return f"{confirm}\n\n{guide}"

    # ─────────── STEP 4: 구매가이드 전달됨 → 양식 대기 ───────────

    def _step4_guide_and_form(self, state: ReviewerState, message: str) -> str:
        """구매 가이드가 이미 전달된 상태. 양식 파싱 시도."""
        # 양식 입력이 온 경우 step5로 처리
        return self._step5_form(state, message)

    # ─────────── STEP 5: 양식 접수 ───────────

    def _step5_form(self, state: ReviewerState, message: str) -> str:
        parsed = parse_full_form(message)

        required = ["수취인명", "연락처", "은행", "계좌", "예금주"]
        missing = [f for f in required if not parsed.get(f)]

        if missing:
            # 양식이 아닌 일반 메시지인 경우
            if len(missing) == len(required):
                campaign = state.temp_data.get("campaign", {})
                guide = self._build_purchase_guide(campaign)
                return f"구매 완료 후 양식을 입력해주세요.\n\n{guide}"
            missing_text = "\n".join(f"- {f}" for f in missing)
            return tpl.FORM_MISSING_FIELDS.format(missing_list=missing_text)

        # 양식 저장 + 기존 시트 행 업데이트
        campaign = state.temp_data.get("campaign", {})
        store_ids = state.temp_data.get("store_ids", [])
        campaign_id = campaign.get("캠페인ID", "")

        if not campaign or not store_ids:
            state.step = 0
            return "캠페인 정보가 없습니다. 처음부터 다시 진행해주세요.\n\n" + tpl.WELCOME_BACK.format(name=state.name)

        # 각 아이디별 양식 데이터 업데이트 (이미 step3에서 행 생성됨)
        for sid in store_ids:
            self.reviewers.update_form_data(
                state.name, state.phone, campaign_id, sid, parsed
            )

        state.step = 6
        upload_url = f"{self.web_url}/upload" if self.web_url else "/upload"
        id_list = ", ".join(store_ids)

        return tpl.FORM_RECEIVED.format(
            product_name=campaign.get("상품명", ""),
            id_list=id_list,
            recipient_name=parsed.get("수취인명", state.name),
            upload_url=upload_url,
        )

    # ─────────── STEP 6: 구매캡쳐 대기 ───────────

    def _step6_purchase(self, state: ReviewerState, message: str) -> str:
        choice = parse_menu_choice(message)
        if choice:
            state.step = 0
            return self._step0_menu(state, message)

        upload_url = f"{self.web_url}/upload" if self.web_url else "/upload"
        return tpl.PURCHASE_CAPTURE_REMIND.format(upload_url=upload_url)

    # ─────────── STEP 7: 리뷰캡쳐 대기 ───────────

    def _step7_review(self, state: ReviewerState, message: str) -> str:
        choice = parse_menu_choice(message)
        if choice:
            state.step = 0
            return self._step0_menu(state, message)

        upload_url = f"{self.web_url}/upload" if self.web_url else "/upload"
        return tpl.REVIEW_CAPTURE_REMIND.format(
            upload_url=upload_url,
            deadline=state.temp_data.get("deadline", "확인 필요"),
        )

    # ─────────── STEP 8: 완료 ───────────

    def _step8_done(self, state: ReviewerState, message: str) -> str:
        state.step = 0
        return tpl.ALL_DONE + "\n\n" + tpl.WELCOME_BACK.format(name=state.name)

    # ─────────── 시트 상태 업데이트 헬퍼 ───────────

    def _update_status_by_id(self, name, phone, campaign_id, store_id, new_status):
        """특정 아이디의 시트 행 상태 업데이트"""
        try:
            if not self.reviewers or not self.reviewers.sheets:
                return
            ws = self.reviewers.sheets._get_ws()
            headers = self.reviewers.sheets._get_headers(ws)
            all_rows = ws.get_all_values()

            name_col = self.reviewers.sheets._find_col(headers, "수취인명")
            phone_col = self.reviewers.sheets._find_col(headers, "연락처")
            cid_col = self.reviewers.sheets._find_col(headers, "캠페인ID")
            sid_col = self.reviewers.sheets._find_col(headers, "아이디")
            status_col = self.reviewers.sheets._find_col(headers, "상태")

            for i, row in enumerate(all_rows[1:], start=2):
                if len(row) <= max(name_col, phone_col, cid_col, sid_col):
                    continue
                if (row[name_col] == name and row[phone_col] == phone and
                    row[cid_col] == campaign_id and row[sid_col] == store_id):
                    ws.update_cell(i, status_col + 1, new_status)
                    break
        except Exception as e:
            logger.error(f"상태 업데이트 에러: {e}")

    # ─────────── 구매 가이드 빌더 ───────────

    def _build_purchase_guide(self, campaign: dict) -> str:
        return tpl.PURCHASE_GUIDE.format(
            product_name=campaign.get("상품명", ""),
            store_name=campaign.get("업체명", ""),
            product_link=campaign.get("상품링크", "없음"),
            keyword=campaign.get("키워드", "없음"),
            entry_method=campaign.get("유입방식", "없음"),
            option=campaign.get("옵션", "없음"),
        )

    # ─────────── 포맷팅 ───────────

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
            "신청": "⚪",
            "가이드전달": "🟡",
            "구매내역제출": "🔵",
            "리뷰제출": "🟢",
            "입금완료": "✅",
            "타임아웃취소": "⏰",
            "취소": "⛔",
        }.get(status, "")

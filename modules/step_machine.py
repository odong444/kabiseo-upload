"""
step_machine.py - 핵심 STEP 0~8 대화 로직

STEP 0: 메뉴 선택
STEP 1: 캠페인 선택 (기존 진행 아이디 표시)
STEP 2: 몇 개 계정 진행?
STEP 3: 아이디 수집 (콤마 구분, 중복체크, 부분중복 처리)
STEP 4: 구매 가이드 전달 + 양식 요청
STEP 5: 양식 접수 (수취인명, 연락처, 은행, 계좌, 예금주, 주소)
STEP 6: 구매캡쳐 대기
STEP 7: 리뷰캡쳐 대기
STEP 8: 완료 (입금대기)
"""

import re
import logging
from modules.state_store import StateStore, ReviewerState
from modules.form_parser import parse_menu_choice, parse_campaign_choice, parse_full_form, parse_multiple_forms
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
            return "스토어 아이디를 입력해주세요."
        else:
            return f"스토어 아이디 {count}개를 입력해주세요.\n(콤마로 구분. 예: abc123, def456)"

    # ─────────── STEP 3: 아이디 수집 (콤마/스페이스 구분, 부분중복 처리) ───────────

    def _step3_collect_ids(self, state: ReviewerState, message: str) -> str:
        raw = message.strip()
        if not raw:
            return tpl.ASK_STORE_IDS

        campaign = state.temp_data.get("campaign", {})
        campaign_id = campaign.get("캠페인ID", "")
        account_count = state.temp_data.get("account_count", 1)

        # ── 중복 처리 서브스테이트 ──
        dup_state = state.temp_data.get("dup_state")

        if dup_state == "ask":
            # 유저가 1(줄여서 진행) or 2(대체 아이디 입력) 선택
            if raw in ("1", "1번"):
                valid_ids = state.temp_data.get("valid_ids", [])
                state.temp_data["store_ids"] = valid_ids
                state.temp_data["account_count"] = len(valid_ids)
                self._clear_dup_state(state)
                return self._register_and_guide(state)
            elif raw in ("2", "2번"):
                state.temp_data["dup_state"] = "replace"
                dup_count = len(state.temp_data.get("dup_ids", []))
                return f"대체할 아이디 {dup_count}개를 입력해주세요. (콤마로 구분)"
            else:
                valid_count = len(state.temp_data.get("valid_ids", []))
                dup_count = len(state.temp_data.get("dup_ids", []))
                return (
                    f"1 또는 2를 선택해주세요.\n"
                    f"1️⃣ 중복 제외 {valid_count}개로 진행\n"
                    f"2️⃣ 중복 {dup_count}개를 다른 아이디로 대체"
                )

        if dup_state == "replace":
            new_ids = [x.strip() for x in re.split(r'[,\s]+', raw) if x.strip()]
            dup_count = len(state.temp_data.get("dup_ids", []))
            valid_ids = state.temp_data.get("valid_ids", [])

            if len(new_ids) != dup_count:
                return f"⚠️ {dup_count}개 아이디를 입력해주세요. (현재 {len(new_ids)}개)"

            # 입력 내 중복 체크
            if len(new_ids) != len(set(new_ids)):
                return "⚠️ 중복된 아이디가 있습니다. 다시 입력해주세요."

            # 기존 valid_ids와 중복 체크
            overlap = [sid for sid in new_ids if sid in valid_ids]
            if overlap:
                return f"⚠️ '{overlap[0]}'은(는) 이미 사용 가능한 아이디에 포함되어 있습니다. 다른 아이디를 입력해주세요."

            # 시트 중복 체크
            allow_dup = campaign.get("중복허용", "").strip().upper() in ("Y", "O", "예", "허용")
            if not allow_dup:
                for sid in new_ids:
                    is_dup = self.reviewers.check_duplicate(campaign_id, sid)
                    if is_dup:
                        return (
                            tpl.DUPLICATE_FOUND.format(store_id=sid) +
                            f"\n\n대체할 아이디 {dup_count}개를 다시 입력해주세요."
                        )

            # 기존 valid + 새 아이디 합치기
            valid_ids = state.temp_data.get("valid_ids", [])
            all_ids = valid_ids + new_ids
            state.temp_data["store_ids"] = all_ids
            self._clear_dup_state(state)
            return self._register_and_guide(state)

        # ── 일반 ID 입력 처리 ──
        ids = [x.strip() for x in re.split(r'[,\s]+', raw) if x.strip()]

        if not ids:
            return "아이디를 입력해주세요. (여러 개면 콤마로 구분)"

        # 입력 수 체크
        if len(ids) != account_count:
            return f"⚠️ {account_count}개 아이디를 입력해주세요. (현재 {len(ids)}개 입력됨)\n콤마로 구분하여 입력해주세요."

        # 아이디 내 중복 체크
        if len(ids) != len(set(ids)):
            return "⚠️ 중복된 아이디가 있습니다. 다시 입력해주세요."

        # 시트 중복 체크 (캠페인별 중복허용 설정 확인)
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
                    # 모두 중복
                    dup_list = ", ".join(dup_ids)
                    return f"⚠️ 입력하신 아이디가 모두 중복입니다: {dup_list}\n다시 입력해주세요."

                # 일부 중복 → 선택지 제공
                dup_list = ", ".join(dup_ids)
                valid_list = ", ".join(valid_ids)
                state.temp_data["dup_state"] = "ask"
                state.temp_data["dup_ids"] = dup_ids
                state.temp_data["valid_ids"] = valid_ids

                return (
                    f"⚠️ 중복된 아이디: {dup_list}\n"
                    f"✅ 사용 가능한 아이디: {valid_list}\n\n"
                    f"어떻게 진행하시겠습니까?\n"
                    f"1️⃣ 중복 제외 {len(valid_ids)}개로 진행\n"
                    f"2️⃣ 중복 {len(dup_ids)}개를 다른 아이디로 대체"
                )

        # 모두 통과
        state.temp_data["store_ids"] = ids
        return self._register_and_guide(state)

    def _clear_dup_state(self, state: ReviewerState):
        """중복 처리 임시 데이터 정리"""
        state.temp_data.pop("dup_state", None)
        state.temp_data.pop("dup_ids", None)
        state.temp_data.pop("valid_ids", None)

    def _register_and_guide(self, state: ReviewerState) -> str:
        """아이디 등록 + 가이드 전달"""
        campaign = state.temp_data.get("campaign", {})
        campaign_id = campaign.get("캠페인ID", "")
        ids = state.temp_data.get("store_ids", [])

        # 시트에 각 아이디별 "신청" 상태로 등록
        for sid in ids:
            self.reviewers.register(state.name, state.phone, campaign, sid)

        # 상태 "가이드전달"로 업데이트
        for sid in ids:
            self._update_status_by_id(state.name, state.phone, campaign_id, sid, "가이드전달")

        state.step = 4
        state.temp_data["submitted_ids"] = []
        id_summary = ", ".join(ids)
        confirm = f"✅ 아이디 확인: {id_summary}"

        # 구매 가이드 자동 전달 (기존 계좌정보 + 결제금액 자동 포함)
        guide = self._build_purchase_guide(campaign, state.name, state.phone, ids)

        # 다중 계정 안내
        if len(ids) > 1:
            confirm += f"\n\n📋 {len(ids)}개 계정 각각 양식을 제출해주세요."

        return f"{confirm}\n\n{guide}"

    # ─────────── STEP 4: 구매가이드 전달됨 → 양식 대기 ───────────

    def _step4_guide_and_form(self, state: ReviewerState, message: str) -> str:
        """구매 가이드가 이미 전달된 상태. 양식 파싱 시도."""
        # 양식 입력이 온 경우 step5로 처리
        return self._step5_form(state, message)

    # ─────────── STEP 5: 양식 접수 (아이디별 개별 처리, 다중 양식 한번에 가능) ───────────

    def _step5_form(self, state: ReviewerState, message: str) -> str:
        campaign = state.temp_data.get("campaign", {})
        store_ids = state.temp_data.get("store_ids", [])
        submitted_ids = state.temp_data.get("submitted_ids", [])
        remaining_ids = [sid for sid in store_ids if sid not in submitted_ids]

        if not campaign or not store_ids:
            state.step = 0
            return "캠페인 정보가 없습니다. 처음부터 다시 진행해주세요.\n\n" + tpl.WELCOME_BACK.format(name=state.name)

        # 다중 양식 감지 (아이디 필드가 2개 이상이면 분할 파싱)
        forms = parse_multiple_forms(message)

        # 양식이 없으면 단일 파싱 시도
        if not forms:
            parsed = parse_full_form(message)
            required = ["수취인명", "연락처", "은행", "계좌", "예금주"]
            missing = [f for f in required if not parsed.get(f)]

            if len(missing) == len(required):
                form_template = self._build_form_template(
                    campaign, state.name, state.phone, remaining_ids
                )
                return f"구매 완료 후 양식을 입력해주세요.\n\n{form_template}"
            missing_text = "\n".join(f"- {f}" for f in missing)
            form_template = self._build_form_template(
                campaign, state.name, state.phone, remaining_ids
            )
            return tpl.FORM_MISSING_FIELDS.format(
                missing_list=missing_text,
                form_template=form_template,
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

            # 아이디 매칭
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

            # 결제금액 자동 설정 + 시트 업데이트
            parsed["결제금액"] = campaign.get("결제금액", "")
            self.reviewers.update_form_data(
                state.name, state.phone, campaign_id, target_id, parsed
            )

            submitted_ids.append(target_id)
            remaining_ids = [sid for sid in store_ids if sid not in submitted_ids]
            results.append(target_id)

        state.temp_data["submitted_ids"] = submitted_ids

        # 응답 조합
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
            return "\n\n".join(response_parts)

        if not results:
            # 에러만 있고 성공한 양식이 없는 경우
            form_template = self._build_form_template(
                campaign, state.name, state.phone, new_remaining or store_ids
            )
            response_parts.append(f"\n양식을 다시 제출해주세요:\n\n{form_template}")
            return "\n\n".join(response_parts)

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
        return "\n\n".join(response_parts)

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

    def _build_form_template(self, campaign: dict, name: str, phone: str,
                              store_ids: list = None) -> str:
        """양식 템플릿 생성 (기존 계좌정보 자동 채움, 수취인/연락처는 비워둠)"""
        prev_info = {}
        try:
            if self.reviewers and self.reviewers.sheets:
                prev_info = self.reviewers.sheets.get_user_prev_info(name, phone)
        except Exception as e:
            logger.error(f"기존 정보 조회 에러: {e}")

        lines = []

        # 다중 계정이면 아이디 필드 포함
        if store_ids and len(store_ids) > 1:
            lines.append("아이디: ")
        elif store_ids and len(store_ids) == 1:
            lines.append(f"아이디: {store_ids[0]}")

        lines += [
            "수취인명: ",
            "연락처: ",
            f"은행: {prev_info.get('은행', '')}",
            f"계좌: {prev_info.get('계좌', '')}",
            f"예금주: {prev_info.get('예금주', '')}",
            f"주소: {prev_info.get('주소', '')}",
        ]
        return "\n".join(lines)

    def _build_purchase_guide(self, campaign: dict, name: str, phone: str,
                              store_ids: list = None) -> str:
        form_template = self._build_form_template(campaign, name, phone, store_ids)
        payment_amount = campaign.get("결제금액", "확인필요")
        review_guide = campaign.get("리뷰가이드", "").strip() or "자율"

        return tpl.PURCHASE_GUIDE.format(
            product_name=campaign.get("상품명", ""),
            store_name=campaign.get("업체명", ""),
            product_link=campaign.get("상품링크", "없음"),
            keyword=campaign.get("키워드", "없음"),
            entry_method=campaign.get("유입방식", "없음"),
            option=campaign.get("옵션", "없음"),
            payment_amount=payment_amount,
            review_guide=review_guide,
            form_template=form_template,
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

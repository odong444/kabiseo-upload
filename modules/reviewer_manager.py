"""
reviewer_manager.py - 리뷰어 관리

리뷰어 등록, 조회, 상태 관리.
"""

import logging
from modules.sheets_manager import SheetsManager
from modules.utils import today_str, safe_int

logger = logging.getLogger(__name__)


class ReviewerManager:
    """리뷰어 관리"""

    def __init__(self, sheets: SheetsManager):
        self.sheets = sheets

    def check_duplicate(self, campaign_id: str, store_id: str) -> bool:
        """캠페인+아이디 중복 체크"""
        return self.sheets.check_duplicate(campaign_id, store_id)

    def register(self, name: str, phone: str, campaign: dict, store_id: str,
                 form_data: dict = None) -> int:
        """리뷰어 신규 등록 → 시트에 행 추가

        name/phone: 진행자(로그인) 정보
        수취인명/연락처: 양식 제출 시 별도 입력 (진행자 ≠ 수취인 가능)
        """
        fd = form_data or {}
        data = {
            "캠페인ID": campaign.get("캠페인ID", ""),
            "업체명": campaign.get("업체명", ""),
            "날짜": today_str(),
            "제품명": campaign.get("상품명", ""),
            "수취인명": fd.get("수취인명", ""),
            "연락처": fd.get("연락처", ""),
            "은행": fd.get("은행", ""),
            "계좌": fd.get("계좌", ""),
            "예금주": fd.get("예금주", ""),
            "결제금액": campaign.get("결제금액", ""),
            "아이디": store_id,
            "주소": fd.get("주소", ""),
            "닉네임": fd.get("닉네임", ""),
            "진행자이름": name,
            "진행자연락처": phone,
            "상태": "신청",
            "리뷰비": campaign.get("리뷰비", ""),
        }
        self.sheets.add_reviewer_row(data)
        logger.info(f"리뷰어 등록: {name} ({phone}) - {campaign.get('상품명', '')} [{store_id}]")
        return 1

    def update_form_data(self, name: str, phone: str, campaign_id: str,
                         store_id: str, form_data: dict, campaign: dict = None):
        """기존 시트 행에 양식 데이터 업데이트

        name/phone: 진행자(로그인) 정보로 행 검색
        campaign: 캠페인 데이터 (리뷰비, 리뷰기한일수 등)
        """
        ws = self.sheets._get_ws()
        headers = self.sheets._get_headers(ws)
        all_rows = ws.get_all_values()

        # 진행자이름+진행자연락처로 검색 (수취인명과 다를 수 있음)
        jn_col = self.sheets._find_col(headers, "진행자이름")
        jp_col = self.sheets._find_col(headers, "진행자연락처")
        rn_col = self.sheets._find_col(headers, "수취인명")
        rp_col = self.sheets._find_col(headers, "연락처")
        cid_col = self.sheets._find_col(headers, "캠페인ID")
        sid_col = self.sheets._find_col(headers, "아이디")

        camp = campaign or {}

        for i, row in enumerate(all_rows[1:], start=2):
            if cid_col < 0 or sid_col < 0 or len(row) <= max(cid_col, sid_col):
                continue
            if row[cid_col] != campaign_id or row[sid_col] != store_id:
                continue

            # 진행자 매칭
            matched = False
            if jn_col >= 0 and jp_col >= 0 and len(row) > max(jn_col, jp_col):
                if row[jn_col] == name and row[jp_col] == phone:
                    matched = True
            if not matched and rn_col >= 0 and rp_col >= 0 and len(row) > max(rn_col, rp_col):
                if row[rn_col] == name and row[rp_col] == phone:
                    matched = True

            if not matched:
                continue

            # 양식 필드 업데이트
            review_fee = safe_int(camp.get("리뷰비", 0))
            purchase_amount = safe_int(form_data.get("결제금액", "") or camp.get("결제금액", 0))
            deposit_amount = review_fee + purchase_amount if (review_fee or purchase_amount) else ""
            update_fields = {
                "수취인명": form_data.get("수취인명", ""),
                "연락처": form_data.get("연락처", ""),
                "은행": form_data.get("은행", ""),
                "계좌": form_data.get("계좌", ""),
                "예금주": form_data.get("예금주", ""),
                "주소": form_data.get("주소", ""),
                "닉네임": form_data.get("닉네임", ""),
                "결제금액": form_data.get("결제금액", ""),
                "리뷰비": str(review_fee) if review_fee else "",
                "입금금액": str(deposit_amount) if deposit_amount else "",
            }

            # 리뷰기한 계산 (오늘 + 리뷰기한일수)
            deadline_days = safe_int(camp.get("리뷰기한일수", 0))
            if deadline_days > 0:
                from datetime import timedelta
                from modules.utils import now_kst
                deadline = now_kst() + timedelta(days=deadline_days)
                update_fields["리뷰기한"] = deadline.strftime("%Y-%m-%d")

            for col_name, value in update_fields.items():
                if value:
                    col = self.sheets._find_col(headers, col_name)
                    if col >= 0:
                        ws.update_cell(i, col + 1, value)
            break
        logger.info(f"양식 업데이트: {name} - {store_id}")

    def get_items(self, name: str, phone: str) -> dict:
        """리뷰어 진행현황"""
        return self.sheets.get_reviewer_items(name, phone)

    def get_payments(self, name: str, phone: str) -> dict:
        """입금현황"""
        return self.sheets.get_payment_info(name, phone)

    def search(self, query: str) -> list[dict]:
        """이름/연락처/아이디로 검색"""
        all_reviewers = self.sheets.get_all_reviewers()
        results = []
        q = query.lower()
        for r in all_reviewers:
            if (q in r.get("수취인명", "").lower() or
                q in r.get("연락처", "") or
                q in r.get("아이디", "").lower()):
                results.append(r)
        return results

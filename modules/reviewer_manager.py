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

        form_data: {수취인명, 연락처, 은행, 계좌, 예금주, 주소, 닉네임, 결제금액}
        """
        fd = form_data or {}
        data = {
            "캠페인ID": campaign.get("캠페인ID", ""),
            "업체명": campaign.get("업체명", ""),
            "날짜": today_str(),
            "제품명": campaign.get("상품명", ""),
            "수취인명": fd.get("수취인명", name),
            "연락처": fd.get("연락처", phone),
            "은행": fd.get("은행", ""),
            "계좌": fd.get("계좌", ""),
            "예금주": fd.get("예금주", name),
            "결제금액": fd.get("결제금액", ""),
            "아이디": store_id,
            "주소": fd.get("주소", ""),
            "닉네임": fd.get("닉네임", ""),
            "상태": "가이드전달",
            "리뷰비": campaign.get("리뷰비", ""),
        }
        self.sheets.add_reviewer_row(data)
        logger.info(f"리뷰어 등록: {fd.get('수취인명', name)} ({fd.get('연락처', phone)}) - {campaign.get('상품명', '')}")
        return 1

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

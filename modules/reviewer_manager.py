"""
reviewer_manager.py - 리뷰어 관리

리뷰어 등록, 조회, 상태 관리.
"""

import logging
from modules.sheets_manager import SheetsManager
from modules.utils import today_str

logger = logging.getLogger(__name__)


class ReviewerManager:
    """리뷰어 관리"""

    def __init__(self, sheets: SheetsManager):
        self.sheets = sheets

    def register(self, name: str, phone: str, campaign: dict, store_id: str) -> int:
        """리뷰어 신규 등록 → 시트에 행 추가"""
        data = {
            "이름": name,
            "연락처": phone,
            "아이디": store_id,
            "예금주": name,  # 기본값
            "제품명": campaign.get("제품명", ""),
            "스토어명": campaign.get("스토어명", ""),
            "옵션": campaign.get("옵션", ""),
            "캠페인ID": campaign.get("캠페인ID", ""),
            "상태": "가이드전달",
            "유입방식": campaign.get("유입방식", ""),
            "리뷰제공": campaign.get("리뷰제공", ""),
        }
        self.sheets.add_reviewer_row(data)
        logger.info(f"리뷰어 등록: {name} ({phone}) - {campaign.get('제품명', '')}")
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
            if (q in r.get("이름", "").lower() or
                q in r.get("연락처", "") or
                q in r.get("아이디", "").lower()):
                results.append(r)
        return results

"""
reviewer_manager.py - 리뷰어 관리

리뷰어 등록, 조회, 상태 관리.
"""

import re
import logging
from modules.utils import today_str, safe_int

logger = logging.getLogger(__name__)


def _format_phone(raw: str) -> str:
    """전화번호 포맷: 01012341234 → 010-1234-1234"""
    digits = re.sub(r"[^0-9]", "", raw)
    if len(digits) == 11:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    return raw


class ReviewerManager:
    """리뷰어 관리"""

    def __init__(self, db):
        self.db = db

    def check_duplicate(self, campaign_id: str, store_id: str) -> bool:
        """캠페인+아이디 중복 체크"""
        return self.db.check_duplicate(campaign_id, store_id)

    def register(self, name: str, phone: str, campaign: dict, store_id: str,
                 form_data: dict = None) -> int:
        """리뷰어 신규 등록 → DB에 행 추가"""
        fd = form_data or {}
        data = {
            "캠페인ID": campaign.get("캠페인ID", ""),
            "업체명": campaign.get("업체명", ""),
            "날짜": today_str(),
            "제품명": campaign.get("상품명", ""),
            "수취인명": fd.get("수취인명", ""),
            "연락처": _format_phone(fd.get("연락처", "")),
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
        progress_id = self.db.add_progress(data)
        logger.info(f"리뷰어 등록: {name} ({phone}) - {campaign.get('상품명', '')} [{store_id}]")
        return progress_id

    def update_form_data(self, name: str, phone: str, campaign_id: str,
                         store_id: str, form_data: dict, campaign: dict = None):
        """양식 데이터 업데이트"""
        self.db.update_form_data(name, phone, campaign_id, store_id, form_data, campaign)

    def get_items(self, name: str, phone: str) -> dict:
        """리뷰어 진행현황"""
        return self.db.get_reviewer_items(name, phone)

    def get_payments(self, name: str, phone: str) -> dict:
        """입금현황"""
        return self.db.get_payment_info(name, phone)

    def search(self, query: str) -> list[dict]:
        """이름/연락처/아이디로 검색"""
        all_reviewers = self.db.get_all_reviewers()
        results = []
        q = query.lower()
        for r in all_reviewers:
            if (q in r.get("수취인명", "").lower() or
                q in r.get("연락처", "") or
                q in r.get("아이디", "").lower()):
                results.append(r)
        return results

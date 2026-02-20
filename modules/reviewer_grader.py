"""
reviewer_grader.py - 리뷰어 등급 관리

리뷰어 활동 이력 기반 등급/신뢰도 관리.
"""

import logging

logger = logging.getLogger(__name__)


GRADES = {
    "신규": {"min_count": 0, "label": "신규"},
    "일반": {"min_count": 1, "label": "일반"},
    "우수": {"min_count": 5, "label": "우수"},
    "VIP": {"min_count": 10, "label": "VIP"},
}


class ReviewerGrader:
    """리뷰어 등급 평가"""

    def __init__(self, sheets_manager):
        self.sheets = sheets_manager

    def get_grade(self, name: str, phone: str) -> str:
        """리뷰어 등급 조회"""
        items = self.sheets.search_by_name_phone(name, phone)
        completed = sum(
            1 for item in items
            if item.get("상태", "") in ("리뷰완료", "정산완료")
        )

        if completed >= 10:
            return "VIP"
        elif completed >= 5:
            return "우수"
        elif completed >= 1:
            return "일반"
        else:
            return "신규"

    def get_stats(self, name: str, phone: str) -> dict:
        """리뷰어 통계"""
        items = self.sheets.search_by_name_phone(name, phone)
        total = len(items)
        completed = sum(
            1 for item in items
            if item.get("상태", "") in ("리뷰완료", "정산완료")
        )
        cancelled = sum(
            1 for item in items
            if item.get("상태", "") == "취소"
        )
        in_progress = total - completed - cancelled

        return {
            "grade": self.get_grade(name, phone),
            "total": total,
            "completed": completed,
            "in_progress": in_progress,
            "cancelled": cancelled,
            "completion_rate": round(completed / total * 100, 1) if total > 0 else 0,
        }

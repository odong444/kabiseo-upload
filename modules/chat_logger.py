"""
chat_logger.py - 대화 이력 저장 (in-memory + optional persistence)

모든 채팅 메시지를 저장하여 관리자 뷰어에서 조회 가능.
"""

import time
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    reviewer_id: str
    sender: str  # "user" or "bot"
    message: str
    timestamp: float = field(default_factory=time.time)
    rating: str = ""  # "good", "bad", ""


class ChatLogger:
    """대화 이력 저장소"""

    def __init__(self):
        self._logs: dict[str, list[ChatMessage]] = {}

    def log(self, reviewer_id: str, sender: str, message: str):
        if reviewer_id not in self._logs:
            self._logs[reviewer_id] = []
        self._logs[reviewer_id].append(
            ChatMessage(reviewer_id=reviewer_id, sender=sender, message=message)
        )

    def get_history(self, reviewer_id: str) -> list[dict]:
        """특정 리뷰어의 대화 이력"""
        messages = self._logs.get(reviewer_id, [])
        return [
            {
                "sender": m.sender,
                "message": m.message,
                "timestamp": m.timestamp,
                "rating": m.rating,
            }
            for m in messages
        ]

    def get_all_reviewer_ids(self) -> list[str]:
        return list(self._logs.keys())

    def get_recent_messages(self, limit: int = 20) -> list[dict]:
        """최근 메시지 (관리자 대시보드용)"""
        all_msgs = []
        for rid, msgs in self._logs.items():
            for m in msgs:
                all_msgs.append({
                    "reviewer_id": rid,
                    "sender": m.sender,
                    "message": m.message,
                    "timestamp": m.timestamp,
                })
        all_msgs.sort(key=lambda x: x["timestamp"], reverse=True)
        return all_msgs[:limit]

    def rate_message(self, reviewer_id: str, timestamp: float, rating: str):
        """메시지에 평가 태깅"""
        messages = self._logs.get(reviewer_id, [])
        for m in messages:
            if abs(m.timestamp - timestamp) < 0.01:
                m.rating = rating
                return True
        return False

    def search(self, keyword: str) -> list[dict]:
        """키워드로 메시지 검색"""
        results = []
        kw = keyword.lower()
        for rid, msgs in self._logs.items():
            for m in msgs:
                if kw in m.message.lower() or kw in rid.lower():
                    results.append({
                        "reviewer_id": rid,
                        "sender": m.sender,
                        "message": m.message,
                        "timestamp": m.timestamp,
                    })
        results.sort(key=lambda x: x["timestamp"], reverse=True)
        return results

"""
chat_logger.py - 대화 이력 저장 (PostgreSQL 영구 보관)

모든 메시지를 DB에 즉시 저장. 서버 재시작 시에도 이력 유지.
3개월 이상 된 로그 자동 삭제.
"""

import time
import logging
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

RETENTION_DAYS = 90  # 3개월 보관
CLEANUP_INTERVAL = 21600  # 6시간마다 정리


@dataclass
class ChatMessage:
    reviewer_id: str
    sender: str  # "user" or "bot"
    message: str
    timestamp: float = field(default_factory=time.time)
    rating: str = ""  # "good", "bad", ""


class ChatLogger:
    """대화 이력 저장소 (PostgreSQL 영구 보관)"""

    def __init__(self):
        self._db = None
        self._running = False
        self._thread = None

    def set_db_manager(self, db_manager):
        """DB 매니저 연결 후 정리 스레드 시작"""
        self._db = db_manager
        if db_manager:
            self._start_cleanup_thread()

    # 하위 호환: set_sheets_manager 호출 시 무시
    def set_sheets_manager(self, sheets_manager):
        pass

    def _start_cleanup_thread(self):
        """주기적 오래된 로그 삭제 스레드"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._thread.start()

    def _cleanup_loop(self):
        while self._running:
            time.sleep(CLEANUP_INTERVAL)
            try:
                if self._db:
                    deleted = self._db.cleanup_old_chat(RETENTION_DAYS)
                    if deleted:
                        logger.info(f"대화이력 정리: {deleted}건 삭제 ({RETENTION_DAYS}일 초과)")
            except Exception as e:
                logger.error(f"대화이력 정리 에러: {e}")

    # ──────── API ────────

    def log(self, reviewer_id: str, sender: str, message: str):
        """메시지 저장 (DB 즉시 저장)"""
        if self._db:
            try:
                self._db.save_chat_message(reviewer_id, sender, message)
            except Exception as e:
                logger.error(f"대화 저장 에러: {e}")

    def get_history(self, reviewer_id: str) -> list[dict]:
        """특정 리뷰어의 대화 이력"""
        if not self._db:
            return []
        try:
            rows = self._db.get_chat_history(reviewer_id)
            return [
                {
                    "sender": r["sender"],
                    "message": r["message"],
                    "timestamp": float(r["timestamp"]) if r.get("timestamp") else 0,
                    "rating": r.get("rating", ""),
                    "time_str": r.get("time_str", ""),
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"대화 조회 에러: {e}")
            return []

    def get_all_reviewer_ids(self) -> list[str]:
        if not self._db:
            return []
        try:
            return self._db.get_chat_reviewer_ids()
        except Exception:
            return []

    def get_recent_messages(self, limit: int = 20) -> list[dict]:
        """최근 메시지 (관리자 대시보드용)"""
        if not self._db:
            return []
        try:
            rows = self._db.get_recent_chat_messages(limit)
            return [
                {
                    "reviewer_id": r["reviewer_id"],
                    "sender": r["sender"],
                    "message": r["message"],
                    "timestamp": float(r["timestamp"]) if r.get("timestamp") else 0,
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"최근 대화 조회 에러: {e}")
            return []

    def rate_message(self, reviewer_id: str, timestamp: float, rating: str):
        """메시지에 평가 태깅"""
        if not self._db:
            return False
        try:
            return self._db.rate_chat_message(reviewer_id, timestamp, rating)
        except Exception:
            return False

    def search(self, keyword: str) -> list[dict]:
        """키워드로 메시지 검색"""
        if not self._db:
            return []
        try:
            rows = self._db.search_chat_messages(keyword)
            return [
                {
                    "reviewer_id": r["reviewer_id"],
                    "sender": r["sender"],
                    "message": r["message"],
                    "timestamp": float(r["timestamp"]) if r.get("timestamp") else 0,
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"대화 검색 에러: {e}")
            return []

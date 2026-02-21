"""
chat_logger.py - 대화 이력 저장 (메모리 + Google Sheets 영구 보관)

인메모리 버퍼 → 주기적으로 시트에 flush → 시작 시 시트에서 로드.
3개월 이상 된 로그 자동 삭제.
"""

import time
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

RETENTION_DAYS = 90  # 3개월 보관
FLUSH_INTERVAL = 120  # 2분마다 flush
CHAT_SHEET_NAME = "대화이력"


@dataclass
class ChatMessage:
    reviewer_id: str
    sender: str  # "user" or "bot"
    message: str
    timestamp: float = field(default_factory=time.time)
    rating: str = ""  # "good", "bad", ""


class ChatLogger:
    """대화 이력 저장소 (메모리 + Sheets 영구 보관)"""

    def __init__(self):
        self._logs: dict[str, list[ChatMessage]] = {}
        self._pending: list[ChatMessage] = []  # flush 대기 큐
        self._sheets_manager = None
        self._running = False
        self._thread = None
        self._lock = threading.Lock()

    def set_sheets_manager(self, sheets_manager):
        """Sheets 매니저 연결 후 시트에서 기존 로그 로드"""
        self._sheets_manager = sheets_manager
        if sheets_manager:
            self._ensure_sheet()
            self._load_from_sheet()
            self._start_flush_thread()

    def _ensure_sheet(self):
        """대화이력 시트가 없으면 생성"""
        try:
            sp = self._sheets_manager.spreadsheet
            try:
                sp.worksheet(CHAT_SHEET_NAME)
            except Exception:
                sp.add_worksheet(title=CHAT_SHEET_NAME, rows=1000, cols=5)
                ws = sp.worksheet(CHAT_SHEET_NAME)
                ws.update('A1:E1', [["timestamp", "reviewer_id", "sender", "message", "rating"]])
                logger.info(f"'{CHAT_SHEET_NAME}' 시트 생성됨")
        except Exception as e:
            logger.error(f"대화이력 시트 생성 에러: {e}")

    def _load_from_sheet(self):
        """시트에서 기존 로그 로드 (3개월 이내)"""
        try:
            sp = self._sheets_manager.spreadsheet
            ws = sp.worksheet(CHAT_SHEET_NAME)
            all_rows = ws.get_all_values()

            if len(all_rows) <= 1:
                logger.info("대화이력 시트: 로드할 데이터 없음")
                return

            cutoff = time.time() - (RETENTION_DAYS * 86400)
            loaded = 0

            for row in all_rows[1:]:
                if len(row) < 4:
                    continue
                try:
                    ts = float(row[0])
                except (ValueError, IndexError):
                    continue

                if ts < cutoff:
                    continue

                rid = row[1]
                msg = ChatMessage(
                    reviewer_id=rid,
                    sender=row[2],
                    message=row[3],
                    timestamp=ts,
                    rating=row[4] if len(row) > 4 else "",
                )
                if rid not in self._logs:
                    self._logs[rid] = []
                self._logs[rid].append(msg)
                loaded += 1

            logger.info(f"대화이력 로드: {loaded}건")
        except Exception as e:
            logger.error(f"대화이력 로드 에러: {e}")

    def _start_flush_thread(self):
        """주기적 flush + 오래된 로그 삭제 스레드"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._thread.start()

    def _flush_loop(self):
        cleanup_counter = 0
        while self._running:
            time.sleep(FLUSH_INTERVAL)
            try:
                self._flush_pending()
            except Exception as e:
                logger.error(f"대화이력 flush 에러: {e}")

            # 6시간마다 오래된 로그 정리 (120초 * 180 = 21600초)
            cleanup_counter += 1
            if cleanup_counter >= 180:
                cleanup_counter = 0
                try:
                    self._cleanup_old()
                except Exception as e:
                    logger.error(f"대화이력 정리 에러: {e}")

    def _flush_pending(self):
        """대기 큐를 시트에 일괄 추가"""
        with self._lock:
            if not self._pending:
                return
            batch = self._pending[:]
            self._pending.clear()

        if not self._sheets_manager:
            return

        try:
            sp = self._sheets_manager.spreadsheet
            ws = sp.worksheet(CHAT_SHEET_NAME)
            rows = []
            for m in batch:
                rows.append([str(m.timestamp), m.reviewer_id, m.sender, m.message, m.rating])
            ws.append_rows(rows, value_input_option="RAW")
            logger.debug(f"대화이력 flush: {len(rows)}건")
        except Exception as e:
            logger.error(f"대화이력 flush 에러: {e}")
            # 실패 시 다시 큐에 넣기
            with self._lock:
                self._pending = batch + self._pending

    def _cleanup_old(self):
        """3개월 이상 된 시트 행 삭제"""
        if not self._sheets_manager:
            return

        try:
            sp = self._sheets_manager.spreadsheet
            ws = sp.worksheet(CHAT_SHEET_NAME)
            all_rows = ws.get_all_values()

            cutoff = time.time() - (RETENTION_DAYS * 86400)
            rows_to_delete = []

            for i, row in enumerate(all_rows[1:], start=2):
                if not row:
                    continue
                try:
                    ts = float(row[0])
                    if ts < cutoff:
                        rows_to_delete.append(i)
                except (ValueError, IndexError):
                    continue

            deleted = 0
            for row_idx in reversed(rows_to_delete):
                try:
                    ws.delete_rows(row_idx)
                    deleted += 1
                except Exception:
                    pass

            # 메모리에서도 정리
            for rid in list(self._logs.keys()):
                self._logs[rid] = [m for m in self._logs[rid] if m.timestamp >= cutoff]
                if not self._logs[rid]:
                    del self._logs[rid]

            if deleted:
                logger.info(f"대화이력 정리: {deleted}건 삭제 (3개월 초과)")
        except Exception as e:
            logger.error(f"대화이력 정리 에러: {e}")

    # ──────── 기존 API (변경 없음) ────────

    def log(self, reviewer_id: str, sender: str, message: str):
        msg = ChatMessage(reviewer_id=reviewer_id, sender=sender, message=message)
        if reviewer_id not in self._logs:
            self._logs[reviewer_id] = []
        self._logs[reviewer_id].append(msg)
        with self._lock:
            self._pending.append(msg)

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

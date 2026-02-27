"""
activity_logger.py - 시스템 활동 로그
"""
import time
import logging
import threading

logger = logging.getLogger(__name__)

LOG_SHEET_NAME = "활동로그"
FLUSH_INTERVAL = 60  # 1분마다 flush

LOG_TYPES = {
    "chat": "💬",
    "form": "✅",
    "photo": "📸",
    "timeout": "⏰",
    "cancel": "❌",
    "campaign": "📋",
    "settlement": "💰",
    "status": "🔄",
    "error": "⚠️",
    "review": "📝",
}

class ActivityLogger:
    def __init__(self):
        self._sheets_manager = None
        self._pending = []
        self._lock = threading.Lock()
        self._running = False

    def set_sheets_manager(self, sheets_manager):
        self._sheets_manager = sheets_manager
        if sheets_manager:
            self._ensure_sheet()
            self._start_flush_thread()

    def _ensure_sheet(self):
        try:
            sp = self._sheets_manager.spreadsheet
            try:
                sp.worksheet(LOG_SHEET_NAME)
            except Exception:
                sp.add_worksheet(title=LOG_SHEET_NAME, rows=2000, cols=8)
                ws = sp.worksheet(LOG_SHEET_NAME)
                ws.update('A1:H1', [["시간", "유형", "아이콘", "이름", "연락처", "캠페인", "아이디", "내용"]])
                logger.info(f"'{LOG_SHEET_NAME}' 시트 생성됨")
        except Exception as e:
            logger.error(f"활동로그 시트 생성 에러: {e}")

    def log(self, log_type: str, name: str = "", phone: str = "",
            campaign: str = "", store_id: str = "", content: str = ""):
        icon = LOG_TYPES.get(log_type, "📌")
        entry = {
            "timestamp": time.time(),
            "type": log_type,
            "icon": icon,
            "name": name,
            "phone": phone,
            "campaign": campaign,
            "store_id": store_id,
            "content": content,
        }
        with self._lock:
            self._pending.append(entry)

    def get_recent_logs(self, limit: int = 100, log_type: str = "") -> list[dict]:
        """최근 로그 조회"""
        if not self._sheets_manager:
            return []
        try:
            sp = self._sheets_manager.spreadsheet
            ws = sp.worksheet(LOG_SHEET_NAME)
            all_rows = ws.get_all_values()
            if len(all_rows) <= 1:
                return []

            logs = []
            for row in all_rows[1:]:
                if len(row) < 8:
                    continue
                entry = {
                    "timestamp": row[0],
                    "type": row[1],
                    "icon": row[2],
                    "name": row[3],
                    "phone": row[4],
                    "campaign": row[5],
                    "store_id": row[6],
                    "content": row[7],
                }
                if log_type and entry["type"] != log_type:
                    continue
                logs.append(entry)

            logs.sort(key=lambda x: x["timestamp"], reverse=True)
            return logs[:limit]
        except Exception as e:
            logger.error(f"활동로그 조회 에러: {e}")
            return []

    def _start_flush_thread(self):
        if self._running:
            return
        self._running = True
        t = threading.Thread(target=self._flush_loop, daemon=True)
        t.start()

    def _flush_loop(self):
        while self._running:
            import time as _time
            _time.sleep(FLUSH_INTERVAL)
            self._flush_pending()

    def _flush_pending(self):
        with self._lock:
            if not self._pending:
                return
            batch = self._pending[:]
            self._pending.clear()

        if not self._sheets_manager:
            return

        try:
            from datetime import datetime
            from modules.utils import KST
            sp = self._sheets_manager.spreadsheet
            ws = sp.worksheet(LOG_SHEET_NAME)
            rows = []
            for e in batch:
                ts = datetime.fromtimestamp(e["timestamp"], tz=KST).strftime("%Y-%m-%d %H:%M:%S")
                rows.append([ts, e["type"], e["icon"], e["name"], e["phone"],
                            e["campaign"], e["store_id"], e["content"]])
            ws.append_rows(rows, value_input_option="RAW")
        except Exception as e:
            logger.error(f"활동로그 flush 에러: {e}")
            with self._lock:
                self._pending = batch + self._pending

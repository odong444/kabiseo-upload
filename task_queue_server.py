"""
task_queue.py - 우선순위 태스크 큐

Railway 웹서버에서 수신한 태스크(친구추가, 독촉, 안내, 홍보, 테스트 등)를
우선순위에 따라 처리하는 큐 + 워커 스레드.

카카오톡 조작은 한 번에 하나만 가능하므로 lock으로 동시 접근 방지.
"""

import sys
sys.stdout.reconfigure(encoding='utf-8')

import ctypes
import ctypes.wintypes
import json
import os
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from modules.utils import setup_logger

logger = setup_logger("task_queue")

KST = timezone(timedelta(hours=9))
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "tasks.db"


class TaskQueue:
    """우선순위 태스크 큐 + 워커 스레드"""

    PRIORITY_URGENT = 1   # 긴급 (친구추가, 테스트발송)
    PRIORITY_NORMAL = 2   # 일반 (안내, 독촉 메시지)
    PRIORITY_LOW = 3      # 예약 (홍보)

    def __init__(self):
        self._lock = threading.Lock()        # 카카오톡 조작 lock
        self._event = threading.Event()       # 새 태스크 알림
        self._running = False
        self._paused = False                  # 일시정지 플래그
        self._worker_thread: Optional[threading.Thread] = None

        # 핸들러 (외부에서 주입)
        self.friend_manager = None
        self.promoter = None
        self._kakao_reset = None      # 카카오톡 초기화 모듈

        self._init_db()
        self._recover_stuck_tasks()
        logger.info("TaskQueue 초기화 완료 (DB: %s)", DB_PATH)

    # ─── DB ───

    def _init_db(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 2,
                    data TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending',
                    result TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_status_priority
                ON tasks(status, priority, created_at)
            """)

    def _recover_stuck_tasks(self):
        with sqlite3.connect(str(DB_PATH)) as conn:
            cur = conn.execute(
                "UPDATE tasks SET status='pending' WHERE status='processing'"
            )
            if cur.rowcount > 0:
                logger.info("복구: processing → pending %d건", cur.rowcount)

    def _conn(self):
        return sqlite3.connect(str(DB_PATH))

    # ─── 중복 체크 ───

    def _is_duplicate_friend_add(self, name: str, phone: str) -> bool:
        """24시간 내 동일 name+phone friend_add가 있는지"""
        cutoff = (datetime.now(KST) - timedelta(hours=24)).isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM tasks"
                " WHERE type='friend_add'"
                " AND json_extract(data, '$.name')=?"
                " AND json_extract(data, '$.phone')=?"
                " AND created_at > ?"
                " AND status IN ('pending','processing','done')"
                " LIMIT 1",
                (name, phone, cutoff)
            ).fetchone()
            return row is not None

    # ─── 태스크 제출 ───

    def submit(self, task_type: str, data: dict, priority: int = 2) -> Optional[str]:
        """태스크 제출. 중복이면 None 반환."""
        if task_type == "friend_add":
            name = data.get("name", "")
            phone = data.get("phone", "")
            if self._is_duplicate_friend_add(name, phone):
                logger.info("중복 friend_add 스킵: %s / %s", name, phone)
                return None

        task_id = str(uuid.uuid4())[:8]
        now = datetime.now(KST).isoformat()

        with self._conn() as conn:
            conn.execute(
                "INSERT INTO tasks (id, type, priority, data, status, created_at)"
                " VALUES (?, ?, ?, ?, 'pending', ?)",
                (task_id, task_type, priority, json.dumps(data, ensure_ascii=False), now)
            )

        logger.info("태스크 등록: [%s] type=%s priority=%d data=%s",
                     task_id, task_type, priority, data)
        self._event.set()
        return task_id

    # ─── 조회 ───

    def get_task(self, task_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, type, priority, data, status, result, created_at, updated_at"
                " FROM tasks WHERE id=?",
                (task_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "type": row[1], "priority": row[2],
            "data": json.loads(row[3]), "status": row[4],
            "result": row[5], "created_at": row[6], "updated_at": row[7],
        }

    def get_pending(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, type, priority, data, status, created_at"
                " FROM tasks WHERE status IN ('pending', 'processing')"
                " ORDER BY status DESC, priority, created_at LIMIT ?",
                (limit,)
            ).fetchall()
        return [
            {"id": r[0], "type": r[1], "priority": r[2],
             "data": json.loads(r[3]), "status": r[4], "created_at": r[5]}
            for r in rows
        ]

    def get_recent(self, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, type, priority, data, status, result, created_at, updated_at"
                " FROM tasks ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [
            {"id": r[0], "type": r[1], "priority": r[2],
             "data": json.loads(r[3]), "status": r[4], "result": r[5],
             "created_at": r[6], "updated_at": r[7]}
            for r in rows
        ]

    # ─── 워커 ───

    def set_friend_manager(self, fm):
        self.friend_manager = fm
        logger.info("FriendManager 연결됨")

    def set_promoter(self, promoter):
        self.promoter = promoter
        logger.info("OpenChatPromoter 연결됨")

    def set_kakao_reset(self, reset):
        """KakaoReset 인스턴스 주입"""
        self._kakao_reset = reset
        logger.info("KakaoReset 연결됨")

    def _safe_reset(self):
        """카카오톡 클린 상태 초기화 (안전 호출)"""
        if not self._kakao_reset:
            return
        try:
            self._kakao_reset.full_reset()
        except Exception as e:
            logger.error("full_reset 실패: %s", e)

    def start_worker(self):
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker, daemon=True, name="task-worker"
        )
        self._worker_thread.start()
        logger.info("태스크 워커 스레드 시작")

    def stop_worker(self):
        self._running = False
        self._event.set()
        if self._worker_thread:
            self._worker_thread.join(timeout=10)
        logger.info("태스크 워커 스레드 중지")

    # ─── 일시정지 / 즉시중지 ───

    def pause(self):
        """일시정지 — 현재 작업은 완료하되 새 작업을 가져오지 않음"""
        self._paused = True
        logger.info("태스크 큐 일시정지")

    def resume(self):
        """재개 — 일시정지 해제"""
        self._paused = False
        self._event.set()  # 워커 즉시 깨움
        logger.info("태스크 큐 재개")

    def emergency_stop(self):
        """즉시중지 — 대기 중인 모든 태스크를 취소 + 카카오톡 초기화"""
        self._paused = True
        cancelled = self.cancel_all_pending()
        logger.info("긴급 중지: pending %d건 취소", cancelled)
        # 카카오톡 강제 초기화
        self._safe_reset()
        return cancelled

    def cancel_all_pending(self) -> int:
        """pending 상태 태스크를 모두 cancelled로 변경"""
        now = datetime.now(KST).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE tasks SET status='cancelled', result='긴급 중지', updated_at=?"
                " WHERE status='pending'",
                (now,)
            )
            return cur.rowcount

    @property
    def is_paused(self) -> bool:
        return self._paused

    def get_queue_status(self) -> dict:
        """큐 상태 요약"""
        with self._conn() as conn:
            pending = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='pending'").fetchone()[0]
            processing = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='processing'").fetchone()[0]
        return {
            "running": self._running,
            "paused": self._paused,
            "pending_count": pending,
            "processing_count": processing,
        }

    def _worker(self):
        """워커 루프: 큐에서 우선순위 순으로 태스크를 꺼내 처리"""
        while self._running:
            self._event.wait(timeout=5)
            self._event.clear()

            while self._running and not self._paused:
                task = self._fetch_next()
                if not task:
                    break
                self._process_task(task)

    def _fetch_next(self) -> Optional[dict]:
        """pending 태스크 중 가장 높은 우선순위 1건을 processing으로 변경"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, type, priority, data FROM tasks"
                " WHERE status='pending'"
                " ORDER BY priority, created_at LIMIT 1"
            ).fetchone()
            if not row:
                return None
            now = datetime.now(KST).isoformat()
            conn.execute(
                "UPDATE tasks SET status='processing', updated_at=? WHERE id=?",
                (now, row[0])
            )
        return {
            "id": row[0], "type": row[1], "priority": row[2],
            "data": json.loads(row[3]),
        }

    def _process_task(self, task: dict):
        """태스크 타입별 처리 (120초 타임아웃, 전/후 카카오톡 초기화)"""
        task_id = task["id"]
        task_type = task["type"]
        data = task["data"]

        logger.info("태스크 처리 시작: [%s] %s", task_id, task_type)

        # GUI 조작이 필요한 태스크인지 확인
        needs_gui = task_type not in ("scan_open_chatrooms",)

        # ── 태스크 전 리셋 (클린 상태 보장) ──
        if needs_gui:
            self._safe_reset()

        # 타임아웃 처리 (기본 120초, join_open_chat은 링크 수에 따라 동적)
        timeout_sec = 120
        if task_type == "join_open_chat":
            link_count = len(data.get("links", []))
            timeout_sec = max(180, link_count * 45)

        result_holder = [None]
        error_holder = [None]

        def _run():
            try:
                with self._lock:
                    if task_type == "friend_add":
                        result_holder[0] = self._handle_friend_add(data)
                    elif task_type == "reminder":
                        result_holder[0] = self._handle_reminder(data)
                    elif task_type == "notification":
                        result_holder[0] = self._handle_notification(data)
                    elif task_type == "test_send":
                        result_holder[0] = self._handle_test_send(data)
                    elif task_type == "promotion":
                        result_holder[0] = self._handle_promotion(data)
                    elif task_type == "join_open_chat":
                        result_holder[0] = self._handle_join_open_chat(data)
                    elif task_type == "scan_open_chatrooms":
                        result_holder[0] = self._handle_scan_open_chatrooms(data)
                    else:
                        result_holder[0] = {"success": False, "message": "unknown type: " + task_type}
            except Exception as e:
                error_holder[0] = e

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        worker.join(timeout=timeout_sec)

        if worker.is_alive():
            logger.error("태스크 타임아웃: [%s] %s (%d초 초과)", task_id, task_type, timeout_sec)
            self._update_task(task_id, "failed", f"타임아웃 ({timeout_sec}초)")
            # Lock은 daemon 스레드에서 해제를 기다려야 하므로 새 lock 생성
            self._lock = threading.Lock()
            # ── 타임아웃 후 리셋 ──
            if needs_gui:
                time.sleep(0.5)
                self._safe_reset()
            return

        if error_holder[0]:
            logger.error("태스크 처리 오류: [%s] %s", task_id, error_holder[0], exc_info=True)
            self._update_task(task_id, "failed", str(error_holder[0]))
            # ── 에러 후 리셋 ──
            if needs_gui:
                self._safe_reset()
            return

        result = result_holder[0] or {"success": False, "message": "결과 없음"}
        status = "done" if result.get("success") else "failed"
        self._update_task(task_id, status, json.dumps(result, ensure_ascii=False))
        logger.info("태스크 완료: [%s] %s -> %s", task_id, task_type, status)

        # ── 정상 완료 후 리셋 (다음 태스크를 위한 클린 상태) ──
        if needs_gui:
            self._safe_reset()

    def _update_task(self, task_id: str, status: str, result: str = None):
        now = datetime.now(KST).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE tasks SET status=?, result=?, updated_at=? WHERE id=?",
                (status, result, now, task_id)
            )

    def cancel_campaign_tasks(self, campaign_id: str) -> int:
        """특정 캠페인의 대기 중 홍보 태스크를 모두 취소."""
        now = datetime.now(KST).isoformat()
        with self._conn() as conn:
            # data는 JSON 문자열이므로 LIKE로 campaign_id 매칭
            cur = conn.execute(
                "UPDATE tasks SET status='cancelled', result=?, updated_at=?"
                " WHERE status='pending' AND type='promotion'"
                " AND data LIKE ?",
                (f"캠페인 중지 ({campaign_id})", now, f'%"campaign_id": "{campaign_id}"%')
            )
            count = cur.rowcount
        if count > 0:
            logger.info("캠페인 [%s] 홍보 태스크 %d건 취소", campaign_id, count)
        return count

    def delete_task(self, task_id: str) -> bool:
        """대기/실패 태스크 삭제. processing 중인 태스크는 삭제 불가."""
        task = self.get_task(task_id)
        if not task:
            return False
        if task["status"] == "processing":
            return False
        with self._conn() as conn:
            conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        return True

    # ─── 유틸 ───

    @staticmethod
    def _format_phone(phone: str) -> str:
        """전화번호를 010-XXXX-XXXX 형식으로 통일."""
        digits = phone.replace("-", "").replace(" ", "")
        if len(digits) == 11 and digits.startswith("010"):
            return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
        if len(digits) == 10 and digits.startswith("01"):
            return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
        return phone  # 변환 불가 시 원본

    # ─── 핸들러 ───

    def _handle_friend_add(self, data: dict) -> dict:
        name = data.get("name", "")
        phone = data.get("phone", "")
        if not name or not phone:
            return {"success": False, "message": "name 또는 phone 누락"}
        if not self.friend_manager:
            return {"success": False, "message": "FriendManager 미연결"}

        phone = self._format_phone(phone)
        # 표시명: "이름 연락처" 형태로 저장 (동명이인 방지)
        display_name = f"{name} {phone}"
        logger.info("친구추가 실행: %s (표시명: %s)", phone, display_name)
        result = self.friend_manager.add_friend(display_name, phone)
        success = result.get("success", False)
        # Railway 웹에 콜백
        self._callback_friend_add(name, phone, success)
        return result

    def _send_with_auto_friend(self, friend_name: str, message: str) -> dict:
        """메시지 전송 시도 → 친구 검색 실패 시 자동 친구추가 → 재시도"""
        result = self.friend_manager.send_message_to_friend(friend_name, message)
        if result.get("success"):
            return result

        # 검색/채팅창 실패 → 친구 미등록일 가능성
        fail_msg = result.get("message", "")
        if "검색" in fail_msg or "채팅창" in fail_msg or "리스트" in fail_msg:
            logger.info("메시지 전송 실패 (%s) → 친구추가 시도: %s", fail_msg, friend_name)
            # friend_name = "이름 010-xxxx-xxxx" 형태에서 파싱
            parts = friend_name.strip().rsplit(" ", 1)
            if len(parts) == 2:
                f_name, f_phone = parts
                display_name = friend_name
                add_result = self.friend_manager.add_friend(display_name, f_phone)
                if add_result.get("success"):
                    logger.info("친구추가 성공 → 메시지 재전송: %s", friend_name)
                    import time
                    time.sleep(2)
                    return self.friend_manager.send_message_to_friend(friend_name, message)
                else:
                    logger.warning("친구추가 실패: %s", add_result.get("message"))
            else:
                logger.warning("friend_name에서 연락처 파싱 실패: %s", friend_name)

        return result

    def _handle_reminder(self, data: dict) -> dict:
        name = data.get("name", "")
        message = data.get("message", "")
        if not name or not message:
            return {"success": False, "message": "name 또는 message 누락"}
        if not self.friend_manager:
            return {"success": False, "message": "FriendManager 미연결"}

        logger.info("독촉 메시지 전송: %s", name)
        return self._send_with_auto_friend(name, message)

    def _handle_notification(self, data: dict) -> dict:
        name = data.get("name", "")
        message = data.get("message", "")
        if not name or not message:
            return {"success": False, "message": "name 또는 message 누락"}
        if not self.friend_manager:
            return {"success": False, "message": "FriendManager 미연결"}

        logger.info("안내 메시지 전송: %s", name)
        return self._send_with_auto_friend(name, message)

    def _handle_test_send(self, data: dict) -> dict:
        """테스트 발송 — 지정 채팅방에 짧은 메시지 전송."""
        room_name = data.get("room_name", "")
        room_type = data.get("room_type", "open")
        message = data.get("message", ",")

        if not room_name:
            return {"success": False, "message": "room_name 누락"}

        if room_type == "open":
            return self._send_to_open_chat(room_name, message)
        else:
            return self._send_to_normal_chat(room_name, message)

    def _handle_promotion(self, data: dict) -> dict:
        """홍보 메시지 발송 — 지정 채팅방에 홍보글 전송."""
        room_name = data.get("room_name", "")
        room_type = data.get("room_type", "open")
        message = data.get("message", "")
        campaign_id = data.get("campaign_id", "")

        if not room_name or not message:
            return {"success": False, "message": "room_name 또는 message 누락"}

        logger.info("홍보 발송: [%s] → %s (%s)", campaign_id or "수동", room_name, room_type)

        if room_type == "open":
            result = self._send_to_open_chat(room_name, message)
        else:
            result = self._send_to_normal_chat(room_name, message)

        # 홍보 이력 기록
        if result.get("success") and campaign_id:
            try:
                self._record_promotion_history(campaign_id, room_name)
            except Exception as e:
                logger.warning("홍보 이력 기록 실패: %s", e)

        return result

    # ─── 채팅방 발송 공통 ───

    def _send_to_open_chat(self, room_name: str, message: str) -> dict:
        """오픈채팅방에 메시지 전송."""
        if not self.promoter:
            # promoter 없으면 동적 생성 시도
            try:
                from modules.kakao_controller import KakaoController
                from modules.open_chat_promoter import OpenChatPromoter
                controller = KakaoController()
                if not controller.find_kakao_window():
                    return {"success": False, "message": "카카오톡 윈도우 없음"}
                self.promoter = OpenChatPromoter(controller)
                logger.info("OpenChatPromoter 동적 생성")
            except Exception as e:
                return {"success": False, "message": f"OpenChatPromoter 초기화 실패: {e}"}

        try:
            if not self.promoter._navigate_to_open_chat():
                return {"success": False, "message": "오픈채팅 탭 전환 실패"}
            if not self.promoter._open_room(room_name):
                self.promoter._restore_to_regular_chat()
                return {"success": False, "message": "채팅방 열기 실패"}
            if self.promoter._send_and_close(message):
                self.promoter._restore_to_regular_chat()
                logger.info("발송 완료: %s (오픈채팅)", room_name)
                return {"success": True}
            else:
                self.promoter._restore_to_regular_chat()
                return {"success": False, "message": "메시지 전송 실패"}
        except Exception as e:
            try:
                self.promoter._restore_to_regular_chat()
            except Exception:
                pass
            return {"success": False, "message": str(e)}

    def _send_to_normal_chat(self, room_name: str, message: str) -> dict:
        """일반채팅방에 메시지 전송 (FriendManager 경유)."""
        if not self.friend_manager:
            return {"success": False, "message": "FriendManager 미연결"}

        logger.info("발송: %s (일반채팅)", room_name)
        result = self.friend_manager.send_message_to_friend(room_name, message)
        return result

    def _callback_friend_add(self, name: str, phone: str, success: bool):
        """친구추가 결과 콜백 (미들웨어 경유 → Railway)."""
        import os
        import requests as _req
        # 미들웨어 경유: PC → 미들웨어 → Railway
        callback_url = os.environ.get("MIDDLEWARE_URL", "http://3.38.161.191:6100")
        if not callback_url:
            # fallback: Railway 직접 (미들웨어 미설정 시)
            callback_url = os.environ.get("RAILWAY_WEB_URL", "https://web-production-1776e.up.railway.app")
        callback_url = callback_url.rstrip("/")
        api_key = os.environ.get("TASK_API_KEY", "_fNmY5SeHyigMgkR5LIngpxBB1gDoZLF")
        try:
            _req.post(
                f"{callback_url}/api/callback/friend-add",
                json={"name": name, "phone": phone, "success": success},
                headers={"X-API-Key": api_key},
                timeout=10,
            )
            logger.info("친구추가 콜백 전송: %s %s → %s (via %s)", name, phone, success, callback_url)
        except Exception as e:
            logger.warning("친구추가 콜백 실패: %s", e)

    def _handle_scan_open_chatrooms(self, data: dict) -> dict:
        """열려있는 카카오톡 채팅창 타이틀을 읽어 오픈채팅 목록 갱신.
        mode='scan': 오픈채팅 전체 교체, mode='add': 기존 목록에 추가만.
        """
        import ctypes
        import ctypes.wintypes

        mode = data.get("mode", "scan")  # scan or add
        user32 = ctypes.windll.user32

        # 1. 열려있는 모든 카카오톡 창 타이틀 수집
        main_hwnd = None
        chat_titles = []

        def enum_cb(hwnd, _):
            nonlocal main_hwnd
            if not user32.IsWindowVisible(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if cls.value != "EVA_Window_Dblclk":
                return True
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            title = buf.value.strip()
            if not title:
                return True
            if title == "카카오톡":
                main_hwnd = hwnd
            else:
                chat_titles.append(title)
            return True

        cb_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int))
        user32.EnumWindows(cb_type(enum_cb), 0)

        if not chat_titles:
            return {"success": False, "message": "열려있는 채팅창이 없습니다"}

        logger.info("열린 채팅창 %d개 발견: %s", len(chat_titles), chat_titles)

        # 2. JSON 업데이트
        chatrooms_path = PROJECT_ROOT / "data" / "open_chatrooms.json"
        existing = {}
        if chatrooms_path.exists():
            try:
                existing = json.loads(chatrooms_path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}

        old_rooms = existing.get("rooms", [])
        normal_rooms = [r for r in old_rooms if r.get("type") == "normal"]
        open_settings = {r["name"]: r for r in old_rooms if r.get("type", "open") == "open"}

        if mode == "add":
            # 추가 모드: 기존 오픈채팅 유지 + 새 것만 추가
            existing_open = [r for r in old_rooms if r.get("type", "open") == "open"]
            existing_names = {r["name"] for r in existing_open}
            added = []
            for title in chat_titles:
                if title not in existing_names:
                    existing_open.append({
                        "name": title, "type": "open", "categories": [],
                        "enabled": False, "active_hours": "09:00~22:00",
                        "cooldown_minutes": 60, "last_sent": None,
                    })
                    added.append(title)
            existing["rooms"] = normal_rooms + existing_open
        else:
            # 스캔 모드: 오픈채팅 전체 교체 (설정 보존)
            new_open = []
            for title in chat_titles:
                if title in open_settings:
                    new_open.append(open_settings[title])
                else:
                    new_open.append({
                        "name": title, "type": "open", "categories": [],
                        "enabled": False, "active_hours": "09:00~22:00",
                        "cooldown_minutes": 60, "last_sent": None,
                    })
            existing["rooms"] = normal_rooms + new_open

        existing["last_scan"] = datetime.now(KST).isoformat()
        chatrooms_path.parent.mkdir(parents=True, exist_ok=True)
        chatrooms_path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        if mode == "add":
            logger.info("오픈채팅 추가: %d개 신규", len(added))
            return {"success": True, "added": added, "count": len(added)}
        else:
            logger.info("오픈채팅 스캔: %d개 (일반 %d개 유지)", len(chat_titles), len(normal_rooms))
            return {"success": True, "rooms": chat_titles, "count": len(chat_titles)}

    def _record_promotion_history(self, campaign_id: str, room_name: str):
        """홍보 이력을 promotion_history.json에 기록."""
        history_path = PROJECT_ROOT / "data" / "promotion_history.json"
        try:
            history = {}
            if history_path.exists():
                history = json.loads(history_path.read_text(encoding="utf-8"))
            key = f"{campaign_id}:{room_name}"
            history[key] = datetime.now(KST).isoformat()
            history_path.write_text(
                json.dumps(history, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("홍보 이력 저장 실패: %s", e)

    # ─── 오픈채팅 링크 자동 입장 ───

    # Win32 콜백 타입 (클래스 레벨)
    _WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )

    def _handle_join_open_chat(self, data: dict) -> dict:
        """오픈채팅 링크로 자동 입장.

        data: {"links": ["https://open.kakao.com/o/gXXX", ...]}
        """
        links = data.get("links", [])
        if not links:
            return {"success": False, "message": "links 누락"}

        valid_links = [l for l in links if l.startswith("https://open.kakao.com/")]
        if not valid_links:
            return {"success": False, "message": "유효한 오픈채팅 링크 없음"}

        user32 = ctypes.windll.user32

        # 카카오톡 메인 윈도우 찾기
        main_hwnd = self._find_kakao_main_hwnd(user32)
        if not main_hwnd:
            return {"success": False, "message": "카카오톡 메인 윈도우 없음"}

        main_pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(main_hwnd, ctypes.byref(main_pid))
        main_pid_val = main_pid.value

        joined = []
        already_joined = []
        password_protected = []
        failed = []

        for i, link in enumerate(valid_links):
            logger.info("오픈채팅 입장 시도 [%d/%d]: %s", i + 1, len(valid_links), link)
            try:
                result = self._process_one_link(link, main_hwnd, main_pid_val, user32)
                if result["status"] == "joined":
                    joined.append({"url": link, "room_name": result.get("room_name", "")})
                    logger.info("  → 입장 성공: %s", result.get("room_name", ""))
                elif result["status"] == "already_joined":
                    already_joined.append({"url": link, "room_name": result.get("room_name", "")})
                    logger.info("  → 이미 참여 중: %s", result.get("room_name", ""))
                elif result["status"] == "password":
                    password_protected.append(link)
                    logger.info("  → 비밀번호 방 (스킵)")
                else:
                    failed.append({"url": link, "error": result.get("error", "알 수 없는 오류")})
                    logger.warning("  → 실패: %s", result.get("error", ""))
            except Exception as e:
                failed.append({"url": link, "error": str(e)})
                logger.error("  → 예외: %s", e)

            # 링크 사이 초기화 + 대기
            if i < len(valid_links) - 1:
                self._safe_reset()
                time.sleep(1.5)

        # 입장 성공한 방 + 이미 참여 중인 방을 open_chatrooms.json에 추가
        all_to_add = joined + [a for a in already_joined if a.get("room_name") and a["room_name"] != "(이름 미확인)"]
        if all_to_add:
            self._auto_add_joined_rooms(all_to_add)

        summary = (f"참여 {len(joined)}, 이미참여 {len(already_joined)}, "
                   f"비밀번호 {len(password_protected)}, 실패 {len(failed)}")
        logger.info("오픈채팅 입장 완료: %s", summary)

        return {
            "success": True,
            "joined": joined,
            "already_joined": already_joined,
            "password_protected": password_protected,
            "failed": failed,
            "summary": summary,
        }

    def _find_kakao_main_hwnd(self, user32) -> int:
        """카카오톡 메인 윈도우(타이틀='카카오톡') hwnd 반환."""
        result = [0]

        def cb(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value.strip() == "카카오톡":
                cls = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls, 256)
                if cls.value == "EVA_Window_Dblclk":
                    result[0] = int(hwnd)
                    return False
            return True

        user32.EnumWindows(self._WNDENUMPROC(cb), 0)
        return result[0]

    def _get_kakao_visible_hwnds(self, main_pid: int, user32) -> set:
        """카카오톡 PID의 모든 visible 윈도우 hwnd 수집."""
        hwnds = set()

        def cb(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == main_pid:
                hwnds.add(int(hwnd))
            return True

        user32.EnumWindows(self._WNDENUMPROC(cb), 0)
        return hwnds

    def _collect_child_texts(self, hwnd: int, user32) -> list:
        """윈도우의 모든 자식 컨트롤 텍스트 수집."""
        texts = []

        def cb(child_hwnd, _):
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(child_hwnd, buf, 512)
            t = buf.value.strip()
            if t:
                texts.append(t)
            return True

        user32.EnumChildWindows(hwnd, self._WNDENUMPROC(cb), 0)
        return texts

    def _collect_child_details(self, hwnd: int, user32) -> list:
        """윈도우의 자식 컨트롤 상세 정보 수집 (디버그용)."""
        details = []

        def cb(child_hwnd, _):
            cbuf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(child_hwnd, cbuf, 256)
            tbuf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(child_hwnd, tbuf, 512)
            cr = ctypes.wintypes.RECT()
            user32.GetWindowRect(child_hwnd, ctypes.byref(cr))
            vis = "V" if user32.IsWindowVisible(child_hwnd) else "H"
            details.append({
                "hwnd": int(child_hwnd), "cls": cbuf.value, "text": tbuf.value,
                "rect": (cr.left, cr.top, cr.right, cr.bottom), "vis": vis,
            })
            return True

        user32.EnumChildWindows(hwnd, self._WNDENUMPROC(cb), 0)
        return details

    def _find_child_by_text(self, hwnd: int, text_substr: str, user32) -> int:
        """텍스트를 포함하는 자식 컨트롤 hwnd 반환. 없으면 0."""
        result = [0]

        def cb(child_hwnd, _):
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(child_hwnd, buf, 512)
            if text_substr in buf.value:
                result[0] = int(child_hwnd)
                return False
            return True

        user32.EnumChildWindows(hwnd, self._WNDENUMPROC(cb), 0)
        return result[0]

    def _read_dialog_texts_uia(self, hwnd: int) -> list:
        """PowerShell + UI Automation으로 다이얼로그 텍스트 읽기."""
        ps_script = (
            "[void][System.Reflection.Assembly]::LoadWithPartialName('UIAutomationClient');"
            "[void][System.Reflection.Assembly]::LoadWithPartialName('UIAutomationTypes');"
            "try{"
            f"$el=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]{hwnd});"
            "if($el){"
            "$cond=[System.Windows.Automation.Condition]::TrueCondition;"
            "$all=$el.FindAll([System.Windows.Automation.TreeScope]::Descendants,$cond);"
            "foreach($i in $all){$n=$i.Current.Name;if($n -and $n.Trim()){Write-Output('T:'+$n.Trim())}}"
            "$rn=$el.Current.Name;if($rn -and $rn.Trim()){Write-Output('ROOT:'+$rn.Trim())}"
            "}}catch{Write-Output('ERR:'+$_.Exception.Message)}"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
                capture_output=True, text=True, encoding="utf-8", timeout=8,
            )
            texts = []
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if line.startswith("T:"):
                    texts.append(line[2:])
                elif line.startswith("ROOT:"):
                    texts.append(line[5:])
                elif line.startswith("ERR:"):
                    logger.warning("UIA 에러: %s", line[4:])
            logger.info("  UIA 텍스트 %d개: %s", len(texts), texts[:10])
            return texts
        except subprocess.TimeoutExpired:
            logger.warning("UIA 텍스트 읽기 타임아웃 (8초)")
            return []
        except Exception as e:
            logger.warning("UIA 텍스트 읽기 실패: %s", e)
            return []

    def _ocr_screen_region(self, left: int, top: int, right: int, bottom: int) -> str:
        """Windows OCR API로 화면 영역의 텍스트 인식 (한국어).

        ocr_region.ps1 스크립트 호출 → CopyFromScreen + WinRT OCR.
        """
        w = right - left
        h = bottom - top
        if w <= 0 or h <= 0:
            return ""

        ps1_path = str(PROJECT_ROOT / "ocr_region.ps1")
        result_path = str(PROJECT_ROOT / "data" / "ocr_result.txt")
        try:
            proc = subprocess.run(
                [
                    "powershell", "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-File", ps1_path,
                    str(left), str(top), str(right), str(bottom), result_path,
                ],
                capture_output=True, timeout=15,
            )
            stdout = (proc.stdout or b"").decode("ascii", errors="replace").strip()
            stderr = (proc.stderr or b"").decode("ascii", errors="replace").strip()

            if proc.returncode != 0:
                logger.warning("OCR 에러 (rc=%d): out=[%s] err=[%s]",
                               proc.returncode, stdout[:200], stderr[:200])
                return ""

            if stdout.startswith("ERR:"):
                logger.warning("OCR 에러: %s", stdout)
                return ""

            # 결과 파일에서 UTF-8로 읽기 (stdout의 OK: 접두사 여부 무관)
            if os.path.exists(result_path):
                with open(result_path, "r", encoding="utf-8-sig") as f:
                    text = f.read().strip()
                try:
                    os.remove(result_path)
                except OSError:
                    pass
                logger.info("  OCR 결과: [%s]", text[:200])
                return text

            logger.warning("OCR 결과 파일 없음: stdout=[%s]", stdout[:200])
            return ""
        except subprocess.TimeoutExpired:
            logger.warning("OCR 타임아웃 (15초)")
            return ""
        except Exception as e:
            logger.warning("OCR 실패: %s", e)
            return ""

    @staticmethod
    def _extract_deep_link(url: str) -> str:
        """open.kakao.com 페이지에서 kakaoopen:// 딥링크 추출.

        HTML의 data-join-scheme 속성에서 딥링크를 가져옴.
        예: kakaoopen://join?l=gP4BWU4f&r=EW
        """
        import re
        import urllib.request

        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0"
        })
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            html = resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            logger.warning("딥링크 추출 실패 (HTTP): %s", e)
            return ""

        # data-join-scheme="kakaoopen://join?l=XXX&r=YY"
        m = re.search(r'data-join-scheme="([^"]+)"', html)
        if m:
            return m.group(1)

        # fallback: kakaoopen:// 패턴 직접 검색
        m = re.search(r'(kakaoopen://[^"\'<>\s]+)', html)
        if m:
            return m.group(1)

        return ""

    def _process_one_link(self, link: str, main_hwnd: int, main_pid: int, user32) -> dict:
        """단일 오픈채팅 링크 처리.

        Returns: {"status": "joined"|"already_joined"|"password"|"failed", ...}
        """
        VK_ESCAPE = 0x1B
        KEYEVENTF_KEYUP = 0x0002
        WM_CLOSE = 0x0010

        # 1. 딥링크 추출 (크롬 거치지 않고 카카오톡 직접 호출)
        deep_link = self._extract_deep_link(link)
        if not deep_link:
            return {"status": "failed", "error": "딥링크 추출 실패 (페이지에서 kakaoopen:// 미발견)"}
        logger.info("  딥링크: %s", deep_link)

        # 2. 기존 윈도우 목록 수집
        existing_hwnds = self._get_kakao_visible_hwnds(main_pid, user32)

        # 3. 딥링크로 카카오톡 직접 호출
        os.startfile(deep_link)

        # 3. 새 윈도우 감지 (최대 10초, 모든 클래스)
        new_hwnd = 0
        for attempt in range(20):
            time.sleep(0.5)
            current_hwnds = self._get_kakao_visible_hwnds(main_pid, user32)
            new_ones = current_hwnds - existing_hwnds

            for h in new_ones:
                # EVA_Window_Dblclk 뿐만 아니라 모든 카카오톡 윈도우 클래스
                cls = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(h, cls, 256)
                buf_t = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(h, buf_t, 256)
                title = buf_t.value.strip()
                # 메인 윈도우("카카오톡")는 제외
                if title != "카카오톡" and int(h) != main_hwnd:
                    new_hwnd = h
                    logger.info("  새 윈도우 감지: hwnd=%d class=[%s] title=[%s] (%.1f초)",
                                h, cls.value, title, attempt * 0.5)
                    break

            if new_hwnd:
                break

        if not new_hwnd:
            return {"status": "failed", "error": "다이얼로그 미감지 (10초 타임아웃)"}

        time.sleep(1.5)  # 다이얼로그 렌더링 대기

        # 4. 다이얼로그 분석
        # 윈도우 타이틀 읽기
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(new_hwnd, buf, 256)
        win_title = buf.value.strip()

        # 다이얼로그 좌표
        dlg_rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(new_hwnd, ctypes.byref(dlg_rect))
        dlg_w = dlg_rect.right - dlg_rect.left
        dlg_h = dlg_rect.bottom - dlg_rect.top
        logger.info("  다이얼로그: title=[%s] rect=(%d,%d,%d,%d) size=%dx%d",
                    win_title, dlg_rect.left, dlg_rect.top,
                    dlg_rect.right, dlg_rect.bottom, dlg_w, dlg_h)

        # 자식 윈도우 상세 (디버그)
        child_details = self._collect_child_details(new_hwnd, user32)
        for cd in child_details:
            logger.info("  child: [%s] text=[%s] rect=%s", cd["cls"], cd["text"], cd["rect"])

        # 5. 다이얼로그 텍스트 읽기 (UIA → OCR fallback)
        uia_texts = self._read_dialog_texts_uia(int(new_hwnd))
        full_text = " ".join(uia_texts)

        # UIA 실패 시 Windows OCR fallback
        if not full_text.strip():
            logger.info("  UIA 미감지 → OCR 시도")
            ocr_text = self._ocr_screen_region(
                dlg_rect.left, dlg_rect.top, dlg_rect.right, dlg_rect.bottom
            )
            if ocr_text:
                full_text = ocr_text

        # ─── 텍스트 기반 분기 (OCR 오인식 대응: regex 사용) ───
        import re
        has_already = bool(re.search(r"참여\s*중", full_text))
        # OCR이 "참여하기"를 "참여다기" 등으로 오인식 → 참여 + 아무글자 + 기
        has_join = bool(re.search(r"참여.기", full_text)) and not has_already
        logger.info("  텍스트 분석: has_join=%s, has_already=%s, text=[%s]",
                    has_join, has_already, full_text[:200])

        # Case A: "참여 중" 감지 → 이미 참여한 방
        if has_already:
            # OCR 텍스트 첫 줄에서 방 이름 추출 시도
            room_name = win_title or ""
            if not room_name and full_text:
                # OCR 텍스트: "방이름 그룹설명... 참여자 N/M ... 참여 중"
                # 첫 줄~"그룹" or "참여자" 전까지가 방 이름
                import re as _re
                m = _re.split(r"그룹|참여자|참여 중|카테고리|\?", full_text.strip(), maxsplit=1)
                if m and m[0].strip():
                    room_name = m[0].strip()[:50]
            logger.info("  '참여 중' 감지 → 이미 참여한 방: %s", room_name)
            user32.PostMessageW(new_hwnd, WM_CLOSE, 0, 0)
            time.sleep(0.5)
            return {"status": "already_joined", "room_name": room_name or "(이름 미확인)"}

        # Case B: "참여하기" (또는 유사 패턴) 감지 → 입장 시도
        if has_join:
            logger.info("  '참여하기' 감지 → 버튼 클릭 시도")
            return self._click_join_button(
                new_hwnd, main_pid, existing_hwnds, child_details,
                dlg_rect, win_title, user32, VK_ESCAPE, KEYEVENTF_KEYUP, WM_CLOSE,
            )

        # Case C: 텍스트를 못 읽었지만 다이얼로그는 있음
        # → 클릭 시도하되, 결과를 정직하게 보고
        logger.warning("  텍스트에서 '참여하기'/'참여 중' 미발견: [%s]", full_text[:200])
        logger.info("  → 버튼 클릭 시도 (텍스트 미감지 상태)")
        result = self._click_join_button(
            new_hwnd, main_pid, existing_hwnds, child_details,
            dlg_rect, win_title, user32, VK_ESCAPE, KEYEVENTF_KEYUP, WM_CLOSE,
        )
        # 텍스트도 없고 클릭 반응도 없으면 → "failed"
        if result["status"] == "click_no_effect":
            result = {"status": "failed", "error": "다이얼로그 텍스트 미감지 + 클릭 반응 없음"}
        return result

    def _click_join_button(self, new_hwnd, main_pid, existing_hwnds,
                           child_details, dlg_rect, win_title, user32,
                           VK_ESCAPE, KEYEVENTF_KEYUP, WM_CLOSE) -> dict:
        """다이얼로그 하단 버튼 영역을 클릭하고 결과 판별.

        3가지 방법을 순차 시도:
        1. Enter 키 (다이얼로그 기본 버튼)
        2. 마우스 클릭 (mouse_event)
        3. 버튼 자식 윈도우에 직접 클릭 메시지 (PostMessage)

        Returns:
            {"status": "joined"|"password"|"click_no_effect", ...}
        """
        VK_RETURN = 0x0D
        WM_LBUTTONDOWN = 0x0201
        WM_LBUTTONUP = 0x0202

        # 버튼 영역 찾기: EVA_ChildWindow 중 높이 40~100px
        btn_child = None
        for cd in child_details:
            if cd["cls"] == "EVA_ChildWindow":
                cr = cd["rect"]
                ch = cr[3] - cr[1]
                if 40 <= ch <= 100:
                    btn_child = cd
                    break

        def _ensure_foreground():
            user32.SetForegroundWindow(new_hwnd)
            time.sleep(0.3)
            fg = user32.GetForegroundWindow()
            if fg != new_hwnd:
                logger.warning("  포그라운드 전환 실패 (fg=%d, target=%d) → 재시도", fg, new_hwnd)
                user32.keybd_event(0x12, 0, 0, 0)  # ALT down
                user32.keybd_event(0x12, 0, KEYEVENTF_KEYUP, 0)  # ALT up
                time.sleep(0.1)
                user32.SetForegroundWindow(new_hwnd)
                time.sleep(0.3)

        def _check_result(method_name: str):
            """클릭/키 후 결과 확인. (dialog 사라짐 or 새 윈도우 → True)"""
            time.sleep(3.0)
            current = self._get_kakao_visible_hwnds(main_pid, user32)
            newer = current - existing_hwnds - {new_hwnd}
            visible = user32.IsWindowVisible(new_hwnd)
            logger.info("  %s 후: 다이얼로그 visible=%s, 새 윈도우 %d개",
                        method_name, visible, len(newer))
            for nh in newer:
                ncls = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(nh, ncls, 256)
                nbuf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(nh, nbuf, 256)
                logger.info("    새 윈도우: hwnd=%d class=[%s] title=[%s]",
                            nh, ncls.value, nbuf.value)
            return not visible or len(newer) > 0, newer, visible

        # ── 디버그: 다이얼로그 스크린샷 저장 ──
        try:
            import subprocess as _sp
            shot_path = str(PROJECT_ROOT / "data" / "join_dialog_debug.png")
            _sp.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 f"Add-Type -AssemblyName System.Drawing; "
                 f"$b=New-Object System.Drawing.Bitmap({dlg_rect.right - dlg_rect.left},{dlg_rect.bottom - dlg_rect.top}); "
                 f"$g=[System.Drawing.Graphics]::FromImage($b); "
                 f"$g.CopyFromScreen({dlg_rect.left},{dlg_rect.top},0,0,"
                 f"[System.Drawing.Size]::new({dlg_rect.right - dlg_rect.left},{dlg_rect.bottom - dlg_rect.top})); "
                 f"$b.Save('{shot_path}'); $g.Dispose(); $b.Dispose()"],
                timeout=5, capture_output=True,
            )
            logger.info("  디버그 스크린샷 저장: %s", shot_path)
        except Exception as e:
            logger.warning("  스크린샷 저장 실패: %s", e)

        # ── 다이얼로그 하단에서 "참여하기" 버튼 클릭 ──
        # 실측: 버튼은 다이얼로그 하단 ~50px 위 (자식 윈도우 영역 아래)
        cx = (dlg_rect.left + dlg_rect.right) // 2
        click_positions = [
            (cx, dlg_rect.bottom - 50),  # 실측 성공 위치
            (cx, dlg_rect.bottom - 35),  # 약간 아래
            (cx, dlg_rect.bottom - 65),  # 약간 위
        ]

        for i, (px, py) in enumerate(click_positions):
            _ensure_foreground()
            logger.info("  클릭 시도 #%d: (%d, %d)", i + 1, px, py)
            user32.SetCursorPos(px, py)
            time.sleep(0.15)
            user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
            user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP

            changed, newer_hwnds, dlg_still_visible = _check_result(f"click#{i+1}")
            if changed:
                return self._process_join_result(
                    new_hwnd, newer_hwnds, dlg_still_visible, win_title,
                    user32, VK_ESCAPE, KEYEVENTF_KEYUP, WM_CLOSE,
                )

        # 모든 방법 실패
        logger.info("  모든 클릭 방법 실패 (click_no_effect)")
        user32.PostMessageW(new_hwnd, WM_CLOSE, 0, 0)
        time.sleep(0.5)
        return {"status": "click_no_effect"}

        # 이 코드는 도달하지 않음 (위 _click_join_button에서 모든 경우 처리)
        return {"status": "failed", "error": "예상치 못한 코드 경로"}

    def _process_join_result(self, dlg_hwnd, newer_hwnds, dlg_still_visible,
                             win_title, user32, VK_ESCAPE, KEYEVENTF_KEYUP, WM_CLOSE) -> dict:
        """클릭/Enter 후 결과를 판별: 입장 성공, 비밀번호, 기타."""
        SKIP_TITLES = {"카카오톡", "프로필 설정", "프로필설정", ""}

        # 새 윈도우 분류: 프로필 설정 / 비밀번호 / 채팅방
        profile_hwnd = None
        profile_rect = None
        password_hwnd = None
        chatroom_hwnd = None
        for nh in newer_hwnds:
            nr = ctypes.wintypes.RECT()
            user32.GetWindowRect(nh, ctypes.byref(nr))
            nw = nr.right - nr.left
            nh_height = nr.bottom - nr.top
            nbuf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(nh, nbuf, 256)
            nt = nbuf.value.strip()
            logger.info("  새 윈도우 분류: hwnd=%d title=[%s] %dx%d", nh, nt, nw, nh_height)

            # 프로필 설정 다이얼로그 (title="프로필 설정", ~300x307)
            if "프로필" in nt:
                profile_hwnd = nh
                profile_rect = nr
                continue

            # 비밀번호 다이얼로그: 작은 창 + Edit + 프로필 아님
            if nw <= 400 and nh_height <= 350:
                nd = self._collect_child_details(nh, user32)
                for c in nd:
                    if "Edit" in c["cls"]:
                        password_hwnd = nh
                        break
                if password_hwnd:
                    continue

            # 나머지 = 채팅방 윈도우 후보
            if nt not in SKIP_TITLES:
                chatroom_hwnd = nh

        # 비밀번호 방 (프로필 설정이 아닌 작은 Edit 창)
        if password_hwnd and not profile_hwnd:
            logger.info("  비밀번호 입력창 감지")
            user32.keybd_event(VK_ESCAPE, 0, 0, 0)
            user32.keybd_event(VK_ESCAPE, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.5)
            user32.PostMessageW(password_hwnd, WM_CLOSE, 0, 0)
            user32.PostMessageW(dlg_hwnd, WM_CLOSE, 0, 0)
            time.sleep(0.5)
            return {"status": "password"}

        # 프로필 설정 다이얼로그 → "확인" 버튼 클릭
        # 레이아웃 2종: 확인만(47%) / 확인+취소 나란히(확인=35%)
        if profile_hwnd and profile_rect:
            pw = profile_rect.right - profile_rect.left
            ph = profile_rect.bottom - profile_rect.top
            btn_y = profile_rect.bottom - 35

            # 확인 버튼 후보 위치: 왼쪽(35%, 확인+취소) → 중앙(47%, 확인만)
            click_positions = [
                (profile_rect.left + int(pw * 0.35), btn_y),
                (profile_rect.left + int(pw * 0.47), btn_y),
            ]

            for attempt, (cx, cy) in enumerate(click_positions):
                user32.SetForegroundWindow(profile_hwnd)
                time.sleep(0.3)
                logger.info("  프로필 '확인' 클릭 #%d: (%d, %d) 프로필창=%dx%d",
                            attempt + 1, cx, cy, pw, ph)
                user32.SetCursorPos(cx, cy)
                time.sleep(0.15)
                user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
                user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
                time.sleep(1.5)
                if not user32.IsWindowVisible(profile_hwnd):
                    logger.info("  프로필 창 닫힘 → 확인 성공")
                    break
                logger.info("  프로필 창 아직 열림 → 다음 위치 시도")

            # 프로필 확인 후 채팅방 윈도우가 새로 열렸을 수 있음 → 다시 스캔
            time.sleep(1.0)

        # ── 채팅방 이름: GetWindowTextW로 직접 읽기 ──
        room_name = ""
        # 1) 이미 분류된 chatroom_hwnd에서 읽기
        if chatroom_hwnd and user32.IsWindowVisible(chatroom_hwnd):
            nbuf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(chatroom_hwnd, nbuf, 256)
            room_name = nbuf.value.strip()
            logger.info("  채팅방 이름 (분류된 윈도우): [%s]", room_name)

        # 2) 못 찾았으면 newer_hwnds 중 visible한 것에서 재시도
        if not room_name:
            for nh in newer_hwnds:
                if not user32.IsWindowVisible(nh):
                    continue
                nbuf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(nh, nbuf, 256)
                nt = nbuf.value.strip()
                if nt not in SKIP_TITLES:
                    room_name = nt
                    logger.info("  채팅방 이름 (재스캔): [%s]", room_name)
                    break

        # 새 채팅창 + 다이얼로그 닫기
        for nh in list(newer_hwnds):
            if user32.IsWindowVisible(nh):
                user32.PostMessageW(nh, WM_CLOSE, 0, 0)
        if dlg_still_visible and user32.IsWindowVisible(dlg_hwnd):
            user32.PostMessageW(dlg_hwnd, WM_CLOSE, 0, 0)
        time.sleep(0.5)

        if room_name:
            return {"status": "joined", "room_name": room_name}
        return {"status": "joined", "room_name": win_title or "(이름 미확인)"}

    def _auto_add_joined_rooms(self, joined: list):
        """입장 성공한 방을 open_chatrooms.json에 추가."""
        chatrooms_path = PROJECT_ROOT / "data" / "open_chatrooms.json"
        existing = {}
        if chatrooms_path.exists():
            try:
                existing = json.loads(chatrooms_path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}

        rooms = existing.get("rooms", [])
        existing_names = {r["name"] for r in rooms}

        added = []
        for info in joined:
            name = info.get("room_name", "").strip()
            if name and name not in existing_names and name != "카카오톡":
                rooms.append({
                    "name": name,
                    "type": "open",
                    "categories": [],
                    "enabled": False,
                    "active_hours": "09:00~22:00",
                    "cooldown_minutes": 60,
                    "last_sent": None,
                })
                added.append(name)
                existing_names.add(name)

        if added:
            existing["rooms"] = rooms
            existing["last_scan"] = datetime.now(KST).isoformat()
            chatrooms_path.parent.mkdir(parents=True, exist_ok=True)
            chatrooms_path.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            logger.info("오픈채팅 자동 추가: %d개 (%s)", len(added), added)

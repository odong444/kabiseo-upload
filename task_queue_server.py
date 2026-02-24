"""
task_queue.py - 우선순위 태스크 큐

Railway 웹서버에서 수신한 태스크(친구추가, 독촉, 안내, 홍보, 테스트 등)를
우선순위에 따라 처리하는 큐 + 워커 스레드.

카카오톡 조작은 한 번에 하나만 가능하므로 lock으로 동시 접근 방지.
"""

import sys
sys.stdout.reconfigure(encoding='utf-8')

import json
import sqlite3
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
        self._worker_thread: Optional[threading.Thread] = None

        # 핸들러 (외부에서 주입)
        self.friend_manager = None
        self.promoter = None

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
                " FROM tasks WHERE status='pending'"
                " ORDER BY priority, created_at LIMIT ?",
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

    def _worker(self):
        """워커 루프: 큐에서 우선순위 순으로 태스크를 꺼내 처리"""
        while self._running:
            self._event.wait(timeout=5)
            self._event.clear()

            while self._running:
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
        """태스크 타입별 처리"""
        task_id = task["id"]
        task_type = task["type"]
        data = task["data"]

        logger.info("태스크 처리 시작: [%s] %s", task_id, task_type)

        try:
            with self._lock:
                if task_type == "friend_add":
                    result = self._handle_friend_add(data)
                elif task_type == "reminder":
                    result = self._handle_reminder(data)
                elif task_type == "notification":
                    result = self._handle_notification(data)
                elif task_type == "test_send":
                    result = self._handle_test_send(data)
                elif task_type == "promotion":
                    result = self._handle_promotion(data)
                elif task_type == "scan_open_chatrooms":
                    result = self._handle_scan_open_chatrooms(data)
                else:
                    result = {"success": False, "message": "unknown type: " + task_type}

            status = "done" if result.get("success") else "failed"
            self._update_task(task_id, status, json.dumps(result, ensure_ascii=False))
            logger.info("태스크 완료: [%s] %s -> %s", task_id, task_type, status)

        except Exception as e:
            logger.error("태스크 처리 오류: [%s] %s", task_id, e, exc_info=True)
            self._update_task(task_id, "failed", str(e))

    def _update_task(self, task_id: str, status: str, result: str = None):
        now = datetime.now(KST).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE tasks SET status=?, result=?, updated_at=? WHERE id=?",
                (status, result, now, task_id)
            )

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

    def _handle_reminder(self, data: dict) -> dict:
        name = data.get("name", "")
        message = data.get("message", "")
        if not name or not message:
            return {"success": False, "message": "name 또는 message 누락"}
        if not self.friend_manager:
            return {"success": False, "message": "FriendManager 미연결"}

        logger.info("독촉 메시지 전송: %s", name)
        result = self.friend_manager.send_message_to_friend(name, message)
        return result

    def _handle_notification(self, data: dict) -> dict:
        name = data.get("name", "")
        message = data.get("message", "")
        if not name or not message:
            return {"success": False, "message": "name 또는 message 누락"}
        if not self.friend_manager:
            return {"success": False, "message": "FriendManager 미연결"}

        logger.info("안내 메시지 전송: %s", name)
        result = self.friend_manager.send_message_to_friend(name, message)
        return result

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
        """Railway 웹에 친구추가 결과 콜백."""
        import os
        import requests as _req
        railway_url = os.environ.get("RAILWAY_WEB_URL", "https://web-production-1776e.up.railway.app")
        api_key = os.environ.get("TASK_API_KEY", "_fNmY5SeHyigMgkR5LIngpxBB1gDoZLF")
        try:
            _req.post(
                f"{railway_url}/api/callback/friend-add",
                json={"name": name, "phone": phone, "success": success},
                headers={"X-API-Key": api_key},
                timeout=10,
            )
            logger.info("친구추가 콜백 전송: %s %s → %s", name, phone, success)
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

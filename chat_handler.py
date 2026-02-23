"""
chat_handler.py - WebSocket 채팅 처리

Flask-SocketIO 이벤트 핸들러.
클라이언트 ↔ StepMachine 연결.
"""

import logging
from flask_socketio import emit, join_room
from flask import request

import models

logger = logging.getLogger(__name__)


def register_handlers(socketio):
    """SocketIO 이벤트 핸들러 등록"""

    @socketio.on("connect")
    def handle_connect():
        logger.info(f"WebSocket 연결: {request.sid}")

    @socketio.on("disconnect")
    def handle_disconnect():
        logger.info(f"WebSocket 연결 해제: {request.sid}")

    @socketio.on("join")
    def handle_join(data):
        """리뷰어 채팅방 입장 + 이력 로드"""
        name = data.get("name", "").strip()
        phone = data.get("phone", "").strip()

        if not name or not phone:
            emit("error", {"message": "이름과 연락처가 필요합니다."})
            return

        reviewer_id = models.state_store.make_id(name, phone)
        join_room(reviewer_id)

        # 리뷰어DB에 등록 (최초 로그인 시 자동 추가)
        if models.db_manager:
            try:
                models.db_manager.upsert_reviewer(name, phone)
            except Exception:
                pass

        # 이전 대화 이력 전송
        history = models.chat_logger.get_history(reviewer_id)
        if history:
            emit("chat_history", {"messages": history})

        # 환영 메시지
        if models.step_machine:
            welcome = models.step_machine.get_welcome(name, phone)
            if welcome:
                if isinstance(welcome, dict):
                    emit("bot_message", welcome)
                    models.chat_logger.log(reviewer_id, "bot", welcome.get("message", ""))
                else:
                    emit("bot_message", {"message": welcome})
                    models.chat_logger.log(reviewer_id, "bot", welcome)
        else:
            emit("bot_message", {
                "message": "안녕하세요! 카비서입니다. 현재 시스템 점검 중입니다. 잠시 후 다시 시도해주세요."
            })

    @socketio.on("user_message")
    def handle_message(data):
        """리뷰어 메시지 처리"""
        name = data.get("name", "").strip()
        phone = data.get("phone", "").strip()
        message = data.get("message", "").strip()

        if not name or not phone or not message:
            return

        reviewer_id = models.state_store.make_id(name, phone)

        if not models.step_machine:
            emit("bot_message", {
                "message": "시스템 점검 중입니다. 잠시 후 다시 시도해주세요."
            }, room=reviewer_id)
            return

        # 타임아웃 경고 클리어
        if models.timeout_manager:
            models.timeout_manager.clear_warning(reviewer_id)

        # 입력중 표시
        emit("bot_typing", {"typing": True}, room=reviewer_id)

        # StepMachine 처리 (dict 또는 str 반환)
        response = models.step_machine.process_message(name, phone, message)

        # 응답 전송
        emit("bot_typing", {"typing": False}, room=reviewer_id)
        if isinstance(response, dict):
            emit("bot_message", response, room=reviewer_id)
        else:
            emit("bot_message", {"message": response}, room=reviewer_id)

    @socketio.on("request_history")
    def handle_history(data):
        """대화 이력 요청"""
        name = data.get("name", "").strip()
        phone = data.get("phone", "").strip()
        if not name or not phone:
            return

        reviewer_id = models.state_store.make_id(name, phone)
        history = models.chat_logger.get_history(reviewer_id)
        emit("chat_history", {"messages": history})

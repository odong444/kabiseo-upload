"""
timeout_manager.py - 20분 타임아웃 관리

신청 후 양식+구매캡쳐 미제출 시 타임아웃 취소.
15분 경고, 20분 취소.
"""

import time
import logging
import threading

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 1200  # 20분
WARNING_SECONDS = 900   # 15분 (경고)


class TimeoutManager:
    """세션 타임아웃 매니저 (WebSocket 알림 + DB 취소)"""

    def __init__(self, state_store, timeout_seconds: int = TIMEOUT_SECONDS,
                 warning_seconds: int = WARNING_SECONDS):
        self.state_store = state_store
        self.timeout = timeout_seconds
        self.warning = warning_seconds
        self._running = False
        self._thread = None
        self._warned = set()  # 이미 경고 보낸 reviewer_id
        self._socketio = None
        self._db_manager = None
        self._chat_logger = None
        self._kakao_notifier = None

    def set_socketio(self, socketio):
        """SocketIO 인스턴스 설정 (앱 시작 후)"""
        self._socketio = socketio

    def set_sheets_manager(self, db_manager):
        """하위 호환: sheets_manager → db_manager"""
        self._db_manager = db_manager

    def set_chat_logger(self, chat_logger):
        self._chat_logger = chat_logger

    def set_kakao_notifier(self, kakao_notifier):
        self._kakao_notifier = kakao_notifier

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._check_loop, daemon=True)
        self._thread.start()
        logger.info(f"타임아웃 매니저 시작 (경고: {self.warning}초, 취소: {self.timeout}초)")

    def stop(self):
        self._running = False

    def clear_warning(self, reviewer_id: str):
        """메시지 수신 시 경고 상태 클리어"""
        self._warned.discard(reviewer_id)

    def _check_loop(self):
        sheet_check_counter = 0
        deadline_check_counter = 0
        while self._running:
            try:
                self._check_all()
            except Exception as e:
                logger.error(f"타임아웃 체크 에러: {e}")

            # DB 기반 타임아웃: 10분마다 (15초 * 40 = 600초)
            sheet_check_counter += 1
            if sheet_check_counter >= 40:
                sheet_check_counter = 0
                try:
                    self._check_db_stale()
                except Exception as e:
                    logger.error(f"DB 타임아웃 체크 에러: {e}")

            # 리뷰 기한 리마인더: 1시간마다 (15초 * 240 = 3600초)
            deadline_check_counter += 1
            if deadline_check_counter >= 240:
                deadline_check_counter = 0
                try:
                    self._check_review_deadlines()
                except Exception as e:
                    logger.error(f"리뷰 기한 체크 에러: {e}")

            time.sleep(15)  # 15초마다 체크

    def _check_all(self):
        for state in self.state_store.all_states():
            if state.step < 4:
                # step 4 이상(가이드 전달 후)부터 타임아웃 적용
                continue

            elapsed = time.time() - state.last_activity
            rid = state.reviewer_id

            # 20분 초과 → 취소
            if elapsed >= self.timeout:
                self._do_timeout_cancel(state)
                continue

            # 15분 초과 → 경고 (1회만)
            if elapsed >= self.warning and rid not in self._warned:
                self._send_warning(state)
                self._warned.add(rid)

    def _send_warning(self, state):
        """15분 경고 메시지 전송"""
        msg = "⏰ 5분 후 미제출 시 신청이 자동 취소됩니다. 양식과 구매캡쳐를 제출해주세요!"
        rid = state.reviewer_id
        logger.info(f"타임아웃 경고: {rid}")

        if self._chat_logger:
            self._chat_logger.log(rid, "bot", msg)

        if self._socketio:
            self._socketio.emit("bot_message", {"message": msg}, room=rid)

        # 카카오톡 경고
        if self._kakao_notifier:
            try:
                campaign = state.temp_data.get("campaign", {})
                product = campaign.get("상품명", "")
                recipient = state.temp_data.get("recipient_name", "")
                sids = ", ".join(state.temp_data.get("store_ids", []))
                self._kakao_notifier.notify_timeout_warning(
                    state.name, state.phone, product, recipient, sids)
            except Exception as e:
                logger.warning(f"카톡 타임아웃 경고 실패: {e}")

    def _do_timeout_cancel(self, state):
        """20분 타임아웃 취소 처리"""
        rid = state.reviewer_id
        campaign = state.temp_data.get("campaign", {})
        campaign_id = campaign.get("캠페인ID", "")
        store_ids = state.temp_data.get("store_ids", [])

        logger.info(f"타임아웃 취소: {rid} (캠페인: {campaign_id}, 아이디: {store_ids})")

        # DB에서 해당 행 타임아웃취소 처리
        if self._db_manager and campaign_id and store_ids:
            try:
                cancelled = self._db_manager.cancel_by_timeout(
                    state.name, state.phone, campaign_id, store_ids
                )
                logger.info(f"DB 취소 {cancelled}건: {rid}")
            except Exception as e:
                logger.error(f"DB 취소 에러: {e}")

        # 알림 메시지
        msg = "⏰ 20분이 경과하여 신청이 자동 취소되었습니다.\n자리가 있으면 다시 신청 가능합니다.\n\n'메뉴'를 입력해주세요."

        if self._chat_logger:
            self._chat_logger.log(rid, "bot", msg)

        if self._socketio:
            self._socketio.emit("bot_message", {"message": msg}, room=rid)

        # 카카오톡 취소 알림
        if self._kakao_notifier:
            try:
                product = campaign.get("상품명", "")
                recipient = state.temp_data.get("recipient_name", "")
                sids = ", ".join(store_ids) if isinstance(store_ids, list) else str(store_ids)
                self._kakao_notifier.notify_timeout_cancelled(
                    state.name, state.phone, product, recipient, sids)
            except Exception as e:
                logger.warning(f"카톡 타임아웃 취소 알림 실패: {e}")

        # 상태 리셋
        state.reset()
        self._warned.discard(rid)

    def _check_review_deadlines(self):
        """리뷰 기한 D-3, D-1 리마인더 발송"""
        if not self._kakao_notifier:
            return
        sent = self._kakao_notifier.send_review_deadline_reminders()
        if sent:
            logger.info(f"리뷰 기한 리마인더 발송: {sent}건")

    def _check_db_stale(self):
        """DB에서 오래된 건 처리: 타임아웃 취소 + 취소 행 삭제"""
        if not self._db_manager:
            return
        cancelled = self._db_manager.cancel_stale_rows(hours=1)
        if cancelled:
            logger.info(f"DB 기반 자동 취소: {cancelled}건")

        deleted = self._db_manager.delete_old_cancelled_rows()
        if deleted:
            logger.info(f"취소 행 자동 삭제: {deleted}건")

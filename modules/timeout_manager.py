"""
timeout_manager.py - 15분 타임아웃 관리

대화 세션의 비활성 타임아웃 처리.
"""

import time
import logging
import threading

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 900  # 15분


class TimeoutManager:
    """세션 타임아웃 매니저"""

    def __init__(self, state_store, timeout_seconds: int = DEFAULT_TIMEOUT,
                 on_expire=None):
        self.state_store = state_store
        self.timeout = timeout_seconds
        self.on_expire = on_expire
        self._running = False
        self._thread = None

    def start(self):
        """백그라운드 타임아웃 체크 시작"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._check_loop, daemon=True)
        self._thread.start()
        logger.info(f"타임아웃 매니저 시작 ({self.timeout}초)")

    def stop(self):
        self._running = False

    def _check_loop(self):
        while self._running:
            try:
                self._check_expired()
            except Exception as e:
                logger.error(f"타임아웃 체크 에러: {e}")
            time.sleep(30)  # 30초마다 체크

    def _check_expired(self):
        """만료된 세션 처리"""
        for state in self.state_store.all_states():
            if state.is_expired(self.timeout) and state.step > 0:
                logger.info(f"세션 만료: {state.reviewer_id}")
                if self.on_expire:
                    self.on_expire(state.reviewer_id)
                state.reset()

    def get_remaining(self, name: str, phone: str) -> int:
        """남은 시간(초) 반환"""
        state = self.state_store.get(name, phone)
        elapsed = time.time() - state.last_activity
        remaining = max(0, self.timeout - int(elapsed))
        return remaining

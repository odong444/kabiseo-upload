"""
state_store.py - 리뷰어별 대화 상태 저장 (in-memory)

각 리뷰어의 현재 STEP, 선택한 캠페인, 임시 데이터 등을 관리.
Railway 재시작 시 초기화됨 (DB 없이 메모리 기반).
"""

import time
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ReviewerState:
    """리뷰어 대화 상태"""
    reviewer_id: str  # "이름_연락처" 조합
    name: str = ""
    phone: str = ""
    step: int = 0
    selected_campaign_id: str = ""
    temp_data: dict = field(default_factory=dict)
    last_activity: float = field(default_factory=time.time)

    def reset(self):
        self.step = 0
        self.selected_campaign_id = ""
        self.temp_data = {}
        self.last_activity = time.time()

    def touch(self):
        self.last_activity = time.time()

    def is_expired(self, timeout_seconds: int = 900) -> bool:
        return (time.time() - self.last_activity) > timeout_seconds


class StateStore:
    """인메모리 상태 저장소"""

    def __init__(self):
        self._states: dict[str, ReviewerState] = {}

    @staticmethod
    def make_id(name: str, phone: str) -> str:
        return f"{name}_{phone}"

    def get(self, name: str, phone: str) -> ReviewerState:
        rid = self.make_id(name, phone)
        if rid not in self._states:
            self._states[rid] = ReviewerState(
                reviewer_id=rid, name=name, phone=phone
            )
        state = self._states[rid]
        state.touch()
        return state

    def get_by_id(self, reviewer_id: str) -> ReviewerState | None:
        return self._states.get(reviewer_id)

    def remove(self, name: str, phone: str):
        rid = self.make_id(name, phone)
        self._states.pop(rid, None)

    def all_states(self) -> list[ReviewerState]:
        return list(self._states.values())

    def cleanup_expired(self, timeout_seconds: int = 900):
        """만료된 세션 정리"""
        expired = [
            rid for rid, state in self._states.items()
            if state.is_expired(timeout_seconds)
        ]
        for rid in expired:
            logger.info(f"세션 만료: {rid}")
            del self._states[rid]
        return len(expired)

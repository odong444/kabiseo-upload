"""
ai_handler.py - 서버PC 릴레이를 통한 AI 응답

Railway → 서버PC(claude -p) → 응답 반환
신뢰도 태그: [UNCERTAIN], [URGENT]
"""

import re
import logging
import requests

from modules.ai_guide import GUIDE as DEFAULT_GUIDE

logger = logging.getLogger(__name__)


def _get_guide() -> str:
    """DB에 저장된 챗봇 가이드 반환, 없으면 하드코딩 기본값 사용"""
    try:
        from models import db_manager
        if db_manager:
            saved = db_manager.get_setting("ai_chatbot_guide", "")
            if saved.strip():
                return saved
    except Exception:
        pass
    return DEFAULT_GUIDE

STEP_DESCRIPTIONS = {
    0: "메뉴 선택 대기",
    1: "캠페인 선택 중",
    2: "계정 수 선택 중",
    3: "스토어 아이디 입력 중",
    4: "구매 가이드 확인 / 옵션 선택 중",
    5: "양식(수취인 정보) 입력 중",
    6: "구매 캡쳐 제출 대기",
    7: "리뷰 캡쳐 제출 대기",
    8: "완료",
    9: "기타 문의 모드",
}

TAG_PATTERN = re.compile(r"\s*\[(UNCERTAIN|URGENT|EDIT)\]\s*", re.IGNORECASE)


class AIHandler:
    """서버PC 릴레이를 통한 AI 응답 핸들러"""

    def __init__(self, relay_url: str, api_key: str = ""):
        self.relay_url = relay_url.rstrip("/")
        self.api_key = api_key

    def get_response(self, user_message: str, context: dict) -> dict:
        """AI 응답 반환 (서버PC 릴레이 경유).

        Returns:
            dict: {"message": str, "confident": bool, "urgent": bool}
            빈 dict이면 AI 응답 실패.
        """
        try:
            reviewer = context.get("reviewer_name", "")
            step_desc = STEP_DESCRIPTIONS.get(context.get("current_step", 0), "메뉴")
            campaign = context.get("campaign_name", "")

            situation = f"리뷰어: {reviewer}, 단계: {step_desc}"
            if campaign:
                situation += f", 캠페인: {campaign}"

            # 학습된 Q&A 사례
            learned_qa = context.get("learned_qa", "")
            learned_section = ""
            if learned_qa:
                learned_section = f"[과거 답변 사례]\n{learned_qa}\n\n"

            prompt = (
                f"아래 서비스 가이드와 과거 답변 사례를 참고해 응답을 작성해줘.\n"
                f"응답 텍스트만 출력하고 코드블록이나 설명은 붙이지 마.\n"
                f"이모지 적당히.\n\n"
                f"{_get_guide()}\n\n"
                f"{learned_section}"
                f"[상황] {situation}\n"
                f"[사용자 메시지] {user_message}\n\n"
                f"응답:"
            )

            headers = {}
            if self.api_key:
                headers["X-API-Key"] = self.api_key

            resp = requests.post(
                f"{self.relay_url}/ai",
                json={"prompt": prompt},
                headers=headers,
                timeout=60,
            )
            resp.raise_for_status()

            data = resp.json()
            raw = data.get("response", "")
            if not raw:
                return {}

            # 태그 파싱
            uncertain = bool(re.search(r"\[UNCERTAIN\]", raw, re.IGNORECASE))
            urgent = bool(re.search(r"\[URGENT\]", raw, re.IGNORECASE))
            edit = bool(re.search(r"\[EDIT\]", raw, re.IGNORECASE))
            clean = TAG_PATTERN.sub("", raw).strip()

            return {
                "message": clean,
                "confident": not uncertain,
                "edit": edit,
                "urgent": urgent,
            }

        except requests.Timeout:
            logger.warning("AI 릴레이 타임아웃 (60초)")
            return {}
        except Exception as e:
            logger.error(f"AI 릴레이 에러: {e}")
            return {}

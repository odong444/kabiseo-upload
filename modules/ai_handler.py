"""
ai_handler.py - Gemini Flash 직접 호출 AI 응답

Railway → Gemini API 직접 호출 (서버PC 릴레이 제거)
신뢰도 태그: [UNCERTAIN], [URGENT], [EDIT]
"""

import os
import re
import logging
import requests

from modules.ai_guide import GUIDE as DEFAULT_GUIDE

logger = logging.getLogger(__name__)

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)


def _get_guide() -> str:
    """DB에 저장된 챗봇 가이드가 있으면 사용, 없으면 하드코딩 기본값"""
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

# 학습 Q&A 최대 건수 (프롬프트 비대화 방지)
MAX_LEARNED_QA = 30


class AIHandler:
    """Gemini Flash 직접 호출 AI 응답 핸들러"""

    def __init__(self, gemini_api_key: str):
        self.api_key = gemini_api_key

    def get_response(self, user_message: str, context: dict) -> dict:
        """AI 응답 반환 (Gemini API 직접 호출).

        Returns:
            dict: {"message": str, "confident": bool, "urgent": bool, "edit": bool}
            빈 dict이면 AI 응답 실패.
        """
        try:
            reviewer = context.get("reviewer_name", "")
            step_desc = STEP_DESCRIPTIONS.get(context.get("current_step", 0), "메뉴")
            campaign = context.get("campaign_name", "")

            situation = f"리뷰어: {reviewer}, 단계: {step_desc}"
            if campaign:
                situation += f", 캠페인: {campaign}"

            # 학습된 Q&A 사례 (최대 MAX_LEARNED_QA건)
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

            payload = {
                "contents": [{
                    "parts": [{"text": prompt}]
                }],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": 512,
                },
            }

            url = f"{_GEMINI_URL}?key={self.api_key}"
            resp = requests.post(url, json=payload, timeout=30)

            if resp.status_code != 200:
                logger.error("Gemini API 에러: %s %s", resp.status_code, resp.text[:300])
                return {}

            data = resp.json()
            raw = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
                .strip()
            )

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
            logger.warning("Gemini API 타임아웃 (30초)")
            return {}
        except Exception as e:
            logger.error(f"Gemini AI 에러: {e}")
            return {}

"""
ai_handler.py - 서버PC 릴레이를 통한 AI 응답

Railway → 서버PC(claude -p) → 응답 반환
"""

import logging
import requests

logger = logging.getLogger(__name__)

CHATBOT_SPEC = """리뷰 체험단 관리 챗봇 '카비서'
- 서비스: 캠페인 신청 → 상품 구매 → 리뷰 작성 → 리뷰비 입금
- 메뉴: 캠페인 목록, 내 진행현황, 사진 제출(하단 탭), 입금 확인
- 타임아웃: 양식 제출 후 20분 내 구매캡쳐 미제출 시 자동 취소
- 리뷰기한: 캠페인마다 다름, 진행현황에서 확인
- 입금: 리뷰 검수 완료 후 순차 입금
- 모르는 내용: '담당자에게 문의해주세요' 안내"""

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
}


class AIHandler:
    """서버PC 릴레이를 통한 AI 응답 핸들러"""

    def __init__(self, relay_url: str, api_key: str = ""):
        self.relay_url = relay_url.rstrip("/")
        self.api_key = api_key

    def get_response(self, user_message: str, context: dict) -> str:
        """AI 응답 반환 (서버PC 릴레이 경유)"""
        try:
            # 컨텍스트
            reviewer = context.get("reviewer_name", "")
            step_desc = STEP_DESCRIPTIONS.get(context.get("current_step", 0), "메뉴")
            campaign = context.get("campaign_name", "")

            situation = f"리뷰어: {reviewer}, 단계: {step_desc}"
            if campaign:
                situation += f", 캠페인: {campaign}"

            # 코드 출력 작업으로 프레이밍
            prompt = (
                f"아래 챗봇 사양에 맞는 응답 텍스트를 작성해줘. "
                f"응답 텍스트만 출력하고 코드블록이나 설명은 붙이지 마. "
                f"짧고 친절하게 2-3문장, 이모지 적당히.\n\n"
                f"[챗봇 사양]\n{CHATBOT_SPEC}\n\n"
                f"[상황] {situation}\n"
                f"[사용자 메시지] {user_message}\n\n"
                f"응답:"
            )

            # 서버PC 릴레이 호출
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
            return data.get("response", "")

        except requests.Timeout:
            logger.warning("AI 릴레이 타임아웃 (60초)")
            return ""
        except Exception as e:
            logger.error(f"AI 릴레이 에러: {e}")
            return ""

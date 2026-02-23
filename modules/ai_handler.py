"""
ai_handler.py - 서버PC 릴레이를 통한 AI 응답

Railway → 서버PC(claude -p) → 응답 반환
신뢰도 태그: [UNCERTAIN], [URGENT]
"""

import re
import logging
import requests

logger = logging.getLogger(__name__)

CHATBOT_SPEC = """리뷰 체험단 관리 챗봇 '카비서'
- 서비스: 캠페인 신청 → 상품 구매 → 리뷰 작성 → 리뷰비 입금
- 메뉴: 캠페인 목록, 내 진행현황, 사진 제출(하단 탭), 입금 확인
- 타임아웃: 양식 제출 후 20분 내 구매캡쳐 미제출 시 자동 취소
- 리뷰기한: 캠페인마다 다름, 진행현황에서 확인
- 입금: 리뷰 검수 완료 후 순차 입금 (5영업일 이내)"""

FAQ_KNOWLEDGE = """[입금관련]
Q: 페이백은 언제되나요?(입금 언제되나요?)
A: 리뷰폼 제출 후 5일이내 들어갑니다 (주말 및 공휴일 제외)

Q: 페이백명이 어떻게 될까요?
A: 진행 날짜 + 스토어명으로 들어갑니다. 스토어명or 업체명이 긴 경우 잘려서 입금됩니다.

[구매관련]
Q: 상품을 못찾겠어요
A: 키워드 입력 및 가격 "***원~***원" 설정 후 검색하시면 쉽게 찾으실 수 있습니다

Q: 상품이 맞는지 모르겠어요
A: 상품 클릭 후 상단링크에서 "smartstore.naver.com/(스토어명)/products/(상품번호)" 가이드에 나와있는 상품번호와 동일한지 확인해보시면 됩니다

Q: 옵션을 잘못구매했어요
A: 취소요청 후 재구매 부탁드립니다. 취소요청 기록을 남겨주시면 담당자에게 전달해드리겠습니다.

[계정관련]
Q: 타계로 해도되나요?
A: 수취인명, 번호, 주소 모두 다를 경우 가능합니다

[배송관련]
Q: 배송이 멈췄어요
A: 배송상태 캡쳐본과 함께 주문자명, 연락처 남겨주시면 빠른 확인 도와드리겠습니다"""

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
                f"아래 챗봇 사양과 FAQ, 과거 답변 사례를 참고해 응답을 작성해줘.\n"
                f"응답 텍스트만 출력하고 코드블록이나 설명은 붙이지 마.\n"
                f"짧고 친절하게 2-3문장, 이모지 적당히.\n\n"
                f"[규칙]\n"
                f"- FAQ나 과거 답변 사례에 비슷한 질문이 있으면 해당 답변을 기반으로 응답\n"
                f"- 답변할 수 없거나 확실하지 않으면 응답 끝에 [UNCERTAIN] 태그\n"
                f"- 결제 오류, 개인정보 유출, 계좌 문제, 배송 사고 등 긴급 상황이면 [URGENT] 태그\n"
                f"- 확실하면 태그 없이 응답\n"
                f"- 폼 수정, 취소 요청, 배송 문제 등 담당자 조치가 필요한 건은 [UNCERTAIN] 태그\n"
                f"- 사용자가 정보 수정(계좌변경, 아이디변경, 주문번호, 수취인, 주소 등)을 요청하면 [EDIT] 태그\n\n"
                f"[챗봇 사양]\n{CHATBOT_SPEC}\n\n"
                f"[FAQ]\n{FAQ_KNOWLEDGE}\n\n"
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

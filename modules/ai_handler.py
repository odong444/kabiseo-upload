"""
ai_handler.py - 서버PC 릴레이를 통한 AI 응답

Railway → 서버PC(claude -p) → 응답 반환
"""

import logging
import requests

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """당신은 '카비서'라는 리뷰 체험단 관리 챗봇의 AI 어시스턴트입니다.

## 카비서 서비스 설명
리뷰어가 체험단 캠페인에 신청하고, 상품을 구매한 뒤 리뷰를 작성하면 리뷰비를 받는 서비스입니다.

## 진행 흐름
1. 캠페인 목록에서 원하는 체험단 선택
2. 진행할 스토어 아이디 입력
3. 구매 가이드 확인 후 상품 구매
4. 양식 제출 (수취인명, 연락처, 은행, 계좌, 예금주, 주소)
5. 구매 캡쳐 사진 제출
6. 리뷰 작성 후 리뷰 캡쳐 사진 제출
7. 검수 후 리뷰비 입금

## 메뉴 안내
- 캠페인 목록: 현재 모집 중인 체험단 확인 및 신청
- 내 진행현황: 현재 진행 중인 체험단 상태 확인
- 사진 제출: 구매/리뷰 캡쳐 사진 업로드 (하단 메뉴 '사진제출' 탭에서도 가능)
- 입금 확인: 리뷰비 입금 현황 확인
- 문의하기: 담당자에게 문의

## 자주 묻는 질문
- 사진 제출 방법: 하단 탭에서 '사진제출' 클릭, 또는 채팅에서 '사진 제출' 메뉴 선택
- 타임아웃: 양식 제출 후 20분 내 구매캡쳐를 제출하지 않으면 자동 취소됩니다
- 리뷰 기한: 캠페인마다 다르며, 진행현황에서 확인 가능합니다
- 입금 시기: 리뷰 검수 완료 후 순차 입금됩니다
- 중복 참여: 캠페인마다 정책이 다릅니다

## 응답 규칙
- 짧고 친절하게 답변 (2-3문장 이내)
- 해당 기능의 메뉴 버튼을 안내해주세요
- 모르는 내용은 "담당자에게 문의해주세요"로 안내
- 절대 거짓 정보를 만들지 마세요
- 이모지를 적당히 사용해주세요"""

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
            # 컨텍스트 구성
            context_lines = []
            if context.get("reviewer_name"):
                context_lines.append(f"리뷰어: {context['reviewer_name']}")
            step = context.get("current_step", 0)
            context_lines.append(f"현재 단계: STEP {step} ({STEP_DESCRIPTIONS.get(step, '알 수 없음')})")
            if context.get("campaign_name"):
                context_lines.append(f"선택한 캠페인: {context['campaign_name']}")
            if context.get("in_progress_count"):
                context_lines.append(f"진행 중인 건: {context['in_progress_count']}건")

            context_text = "\n".join(context_lines)

            # 시스템 프롬프트 + 컨텍스트 + 사용자 메시지를 하나로 합침
            prompt = (
                f"{SYSTEM_PROMPT}\n\n"
                f"---\n\n"
                f"[리뷰어 상황]\n{context_text}\n\n"
                f"[리뷰어 메시지]\n{user_message}"
            )

            # 서버PC 릴레이 호출
            headers = {}
            if self.api_key:
                headers["X-API-Key"] = self.api_key

            resp = requests.post(
                f"{self.relay_url}/ai",
                json={"prompt": prompt},
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()

            data = resp.json()
            return data.get("response", "")

        except requests.Timeout:
            logger.warning("AI 릴레이 타임아웃 (30초)")
            return ""
        except Exception as e:
            logger.error(f"AI 릴레이 에러: {e}")
            return ""

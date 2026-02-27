"""
capture_verifier.py - AI 캡쳐 검수 (Gemini Vision)

구매캡쳐/리뷰캡쳐 업로드 시 자동 검증.
Google Drive 이미지를 다운로드 → Gemini 2.0 Flash로 분석.
"""

import io
import json
import logging
import os
import re
import threading

import requests

logger = logging.getLogger(__name__)

# Gemini API (환경변수에서 로드)
_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
_GEMINI_BASE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)

# ── 기본 프롬프트 ──

PURCHASE_PROMPT_BASE = """이 이미지는 온라인 쇼핑몰(쿠팡/네이버/카카오 등)의 주문 상세 캡쳐입니다.
다음 정보를 추출하고, 캠페인 기준 정보와 대조하여 검수해주세요.
이미지에서 보이지 않거나 잘린 항목은 빈 문자열("")로 응답하세요.
결제금액 등 숫자 값은 콤마 없이 숫자만 입력하세요 (예: "29000").

[필수 추출 항목] - 반드시 확인해야 하는 정보:
주문번호, 수취인명, 수취인연락처, 주소, 결제금액

[선택 추출 항목] - 보이면 추출:
상품명, 수량, 배송유형

JSON 형식으로만 응답:
{
    "주문번호": "",
    "상품명": "",
    "수량": "",
    "결제금액": "",
    "수취인명": "",
    "수취인연락처": "",
    "주소": "",
    "배송유형": "",
    "상품일치": true/false,
    "금액일치": true/false,
    "금액비고": "",
    "플랫폼일치": true/false,
    "문제점": []
}

배송유형은 다음 중 하나로 판별:
- "로켓배송": 쿠팡 로켓배송 마크가 있는 경우
- "로켓와우": 로켓와우 배송
- "판매자배송": 판매자가 직접 배송
- "판매자로켓": 판매자로켓 표시가 있는 경우
- "확인불가": 배송유형을 알 수 없는 경우

대조 검수 규칙 (유연하게 판단):
- 상품일치: 캠페인 상품과 동일/유사 상품인지 판단. 상품명이 약간 다르더라도(약어, 브랜드명 생략, 옵션 표기 차이, 용량/색상 표기 방식 차이 등) 실질적으로 같은 상품이면 true. 완전히 다른 상품이면 false.
- 금액일치: 캠페인 기준 금액과 비교. 쿠폰/할인/적립금 적용으로 소폭 차이(기준 금액의 10% 이내)는 true. 10% 초과 차이는 false. 기준 금액이 제공되지 않은 경우는 true로 처리.
- 금액비고: 금액일치가 true이더라도 기준 금액과 차이가 있으면 그 차이를 설명 (예: "기준 29000원 → 실결제 26500원, 쿠폰 적용 추정"). 차이가 없거나 기준 금액 미제공이면 빈 문자열.
- 플랫폼일치: 캡쳐 화면이 캠페인 지정 플랫폼의 주문 화면인지 확인. 플랫폼 정보가 제공되지 않은 경우는 true로 처리. 아래 플랫폼 식별 가이드를 참고하여 판단.

[플랫폼 식별 가이드]
각 플랫폼의 주문내역/주문상세 페이지 특징:

1. 스마트스토어 / 네이버:
   - "네이버페이 주문관리", "네이버페이 구매내역", "주문상세" 등의 문구
   - URL에 smartstore.naver.com, orders.pay.naver.com, new.pay.naver.com 등
   - 초록색 네이버 로고, 네이버페이 로고
   - "네이버페이 결제", "네이버페이 포인트" 등의 결제수단 표기
   - 캠페인 플랫폼이 "스마트스토어", "네이버", "네이버스토어", "스토어" 등이면 이 플랫폼으로 판단

2. 쿠팡:
   - "쿠팡" 로고, 파란색/빨간색 쿠팡 디자인
   - "로켓배송", "로켓와우", "판매자배송", "판매자로켓" 배송유형 표시
   - "마이쿠팡 > 주문목록", "주문상세" 등의 문구
   - 캠페인 플랫폼이 "쿠팡" 이면 이 플랫폼으로 판단

3. 올리브영:
   - "올리브영" 로고, 초록색 올리브영 디자인
   - "마이페이지 > 주문/배송 조회", "주문상세" 등의 문구
   - 화장품/뷰티 상품 위주
   - 캠페인 플랫폼이 "올리브영" 이면 이 플랫폼으로 판단

4. 11번가:
   - "11번가" 로고, 빨간색 11번가 디자인
   - "마이11번가", "주문/배송", "주문상세" 등의 문구
   - 캠페인 플랫폼이 "11번가" 이면 이 플랫폼으로 판단

5. 지마켓 / G마켓:
   - "G마켓", "Gmarket" 로고, 초록색 지마켓 디자인
   - "마이G마켓", "주문/배송 조회", "주문상세" 등의 문구
   - 캠페인 플랫폼이 "지마켓", "G마켓", "gmarket" 이면 이 플랫폼으로 판단

6. 옥션:
   - "옥션", "Auction" 로고
   - "마이옥션", "주문/배송 조회" 등의 문구
   - 캠페인 플랫폼이 "옥션" 이면 이 플랫폼으로 판단

7. 카카오 / 톡딜:
   - "카카오톡 선물하기", "카카오쇼핑", "톡딜" 등의 문구
   - 노란색 카카오 디자인
   - 캠페인 플랫폼이 "카카오", "톡딜" 이면 이 플랫폼으로 판단

중요: 플랫폼 이름이 정확히 일치하지 않아도 같은 플랫폼 계열이면 일치로 판단하세요.
예) 캠페인 플랫폼 "스마트스토어" → 네이버페이 주문화면이면 일치(true).
예) 캠페인 플랫폼 "네이버" → 스마트스토어 주문화면이면 일치(true).

문제점에는 다음 중 해당하는 것을 배열로 넣어주세요:
- "상품정보_미확인": 상품명이나 이미지가 잘려서 뭘 구매했는지 확인 불가
- "주문번호_미확인": 주문번호가 보이지 않음
- "수취인_미확인": 수취인명 또는 수취인연락처가 보이지 않음
- "주소_미확인": 배송 주소가 보이지 않음
- "결제금액_미확인": 결제금액이 보이지 않음
- "배송유형_불일치": 추가 검수 지침에서 지정한 배송유형과 다른 배송유형으로 구매함 (예: 판매자배송 필수인데 로켓배송으로 구매)
- "상품_불일치": 캠페인 상품과 다른 상품을 구매함
- "금액_불일치": 결제금액이 캠페인 기준 금액과 크게 다름
- "플랫폼_불일치": 캠페인 지정 플랫폼이 아닌 곳에서 구매함
- "정상": 모든 필수 정보가 정상적으로 확인됨

문제점이 없으면 ["정상"]으로 응답하세요."""

REVIEW_PROMPT_BASE = """이 이미지는 온라인 쇼핑몰에 작성된 리뷰 캡쳐입니다.
다음 정보를 확인하고, 캠페인 기준 정보와 대조하여 검수해주세요.

JSON 형식으로만 응답:
{
    "리뷰내용_확인": true/false,
    "리뷰글자수": 0,
    "사진포함": true/false,
    "별점": "",
    "상품일치": true/false,
    "문제점": []
}

대조 검수 규칙:
- 상품일치: 리뷰 대상 상품이 캠페인 상품과 동일/유사한지 확인. 상품명이 화면에 보이면 대조하고, 보이지 않으면 true로 처리.
- 리뷰글자수: 리뷰 텍스트의 대략적인 글자수 (공백 제외).

문제점에는 다음 중 해당하는 것을 배열로 넣어주세요:

[자동반려 대상 - 심각한 문제]:
- "리뷰_아님": 이 이미지가 리뷰 캡쳐가 아닌 것으로 보임
- "상품_불일치": 리뷰 대상 상품이 캠페인 상품과 명확히 다른 상품임
- "리뷰_무성의": 리뷰 텍스트가 10자 이내로 너무 짧거나 무성의함
- "리뷰_반복문자": 같은 글자나 단어가 과도하게 반복됨 (예: "좋아요좋아요좋아요", "ㅋㅋㅋㅋㅋ" 등)
- "리뷰내용_상품불일치": 리뷰 내용이 캠페인 상품과 관련 없는 내용임 (다른 상품에 대한 리뷰)

[담당자 검수요청 대상 - 확인 필요]:
- "리뷰내용_미확인": 리뷰 텍스트가 보이지 않거나 잘림
- "리뷰_글자수부족": 리뷰 텍스트가 기준 글자수보다 약간 부족 (추가 검수 지침에 글자수 기준이 있을 경우)
- "사진_이상": 리뷰에 포함된 사진이 상품과 무관하거나 이상함
- "상품_애매": 리뷰 대상 상품이 캠페인 상품과 유사하지만 확신할 수 없음
- "사진_미첨부": 리뷰용 참고 사진을 제공받았으나 리뷰에 사진이 포함되지 않음
- "정상": 리뷰가 정상적으로 작성되어 있음

문제점이 없으면 ["정상"]으로 응답하세요."""


def _build_prompt(capture_type: str, campaign_info: dict | None,
                  ai_instructions: str = "") -> str:
    """캡쳐 타입 + 캠페인 정보 + AI 지침으로 최종 프롬프트 구성"""
    base = PURCHASE_PROMPT_BASE if capture_type == "purchase" else REVIEW_PROMPT_BASE

    parts = [base]

    # 캠페인 기준 정보 삽입
    if campaign_info:
        ctx_lines = ["\n[캠페인 기준 정보]"]
        if campaign_info.get("상품명"):
            ctx_lines.append(f"- 상품명: {campaign_info['상품명']}")
        if campaign_info.get("업체명"):
            ctx_lines.append(f"- 업체/스토어명: {campaign_info['업체명']}")
        if campaign_info.get("플랫폼"):
            ctx_lines.append(f"- 플랫폼: {campaign_info['플랫폼']}")
        if campaign_info.get("상품금액"):
            ctx_lines.append(f"- 기준 상품금액: {campaign_info['상품금액']}원")
        if campaign_info.get("결제금액"):
            ctx_lines.append(f"- 기준 결제금액: {campaign_info['결제금액']}원")
        if campaign_info.get("옵션"):
            ctx_lines.append(f"- 옵션: {campaign_info['옵션']}")
        if campaign_info.get("캠페인유형"):
            ctx_lines.append(f"- 캠페인유형: {campaign_info['캠페인유형']}")
        if len(ctx_lines) > 1:
            parts.append("\n".join(ctx_lines))

    if ai_instructions:
        parts.append(f"\n추가 검수 지침:\n{ai_instructions}")

    return "\n".join(parts)


# 하위 호환: 기존 변수명 유지
PURCHASE_PROMPT = PURCHASE_PROMPT_BASE
REVIEW_PROMPT = REVIEW_PROMPT_BASE


def _extract_drive_file_id(url: str) -> str | None:
    """Google Drive URL에서 file ID 추출"""
    if not url:
        return None
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    return m.group(1) if m else None


def _download_drive_image(file_id: str) -> tuple[bytes, str] | None:
    """Google Drive에서 이미지 다운로드 (공개 파일)"""
    download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    try:
        resp = requests.get(download_url, timeout=30, allow_redirects=True)
        if resp.status_code != 200:
            logger.warning("Drive 다운로드 실패: status=%s", resp.status_code)
            return None
        content_type = resp.headers.get("Content-Type", "image/jpeg")
        if "image" not in content_type:
            # Google Drive가 HTML 경고 페이지를 반환하는 경우 (대용량 파일)
            # confirm 파라미터로 재시도
            if "text/html" in content_type:
                confirm_url = f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t"
                resp = requests.get(confirm_url, timeout=30, allow_redirects=True)
                content_type = resp.headers.get("Content-Type", "image/jpeg")
                if "image" not in content_type:
                    logger.warning("Drive 다운로드: 이미지가 아님 (%s)", content_type)
                    return None
        return resp.content, content_type
    except Exception as e:
        logger.error("Drive 다운로드 에러: %s", e)
        return None


def _call_gemini(image_bytes: bytes, mime_type: str, prompt: str) -> dict | None:
    """Gemini Vision API 호출"""
    api_key = _GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        logger.error("GEMINI_API_KEY 환경변수가 설정되지 않았습니다")
        return None

    import base64
    b64 = base64.b64encode(image_bytes).decode("utf-8")

    # mime 정리
    if "png" in mime_type:
        mime = "image/png"
    elif "webp" in mime_type:
        mime = "image/webp"
    else:
        mime = "image/jpeg"

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime, "data": b64}},
            ]
        }],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1024,
        },
    }

    url = f"{_GEMINI_BASE_URL}?key={api_key}"
    try:
        resp = requests.post(url, json=payload, timeout=60)
        if resp.status_code != 200:
            logger.error("Gemini API 에러: %s %s", resp.status_code, resp.text[:300])
            return None
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        # markdown 코드블록 제거
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            text = text.rsplit("```", 1)[0].strip()
        return json.loads(text)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.error("Gemini 응답 파싱 에러: %s", e)
        return None
    except Exception as e:
        logger.error("Gemini 호출 에러: %s", e)
        return None


_PROBLEM_MESSAGES = {
    # 구매캡쳐
    "상품정보_미확인": "상품 정보가 확인되지 않습니다",
    "주문번호_미확인": "주문번호가 보이지 않습니다",
    "수취인_미확인": "수취인 정보가 보이지 않습니다",
    "주소_미확인": "배송 주소가 확인되지 않습니다",
    "결제금액_미확인": "결제금액이 확인되지 않습니다",
    "배송유형_불일치": "지정된 배송유형과 다릅니다",
    "상품_불일치": "캠페인 상품과 다른 상품입니다",
    "금액_불일치": "결제금액이 캠페인 기준과 다릅니다",
    "플랫폼_불일치": "캠페인 지정 플랫폼이 아닙니다",
    # 리뷰캡쳐
    "리뷰내용_미확인": "리뷰 내용이 확인되지 않습니다",
    "리뷰_아님": "리뷰 캡쳐가 아닌 것으로 보입니다",
    "리뷰_무성의": "리뷰가 너무 짧거나 무성의합니다",
    "리뷰_반복문자": "같은 글자/단어가 과도하게 반복됩니다",
    "리뷰내용_상품불일치": "리뷰 내용이 캠페인 상품과 관련 없습니다",
    "리뷰_글자수부족": "리뷰 글자수가 기준보다 부족합니다",
    "사진_이상": "리뷰 사진이 상품과 무관하거나 이상합니다",
    "상품_애매": "리뷰 대상 상품이 캠페인 상품과 일치하는지 확인 필요",
    "사진_미첨부": "제공된 리뷰용 사진이 리뷰에 포함되지 않았습니다",
}

# 자동반려 대상 문제점 (심각한 위반 → 제출 자체 차단)
_AUTO_REJECT_PROBLEMS = {
    "배송유형_불일치",
    "리뷰_아님",
    "리뷰_무성의",
    "리뷰_반복문자",
    "리뷰내용_상품불일치",
    "사진_미첨부",
}


def _judge(analysis: dict, capture_type: str) -> dict:
    """Gemini 분석 결과로 최종 판정"""
    problems = analysis.get("문제점", [])
    if not problems or problems == ["정상"]:
        # 금액 소폭 차이 → 확인요청
        if capture_type == "purchase":
            price_note = analysis.get("금액비고", "")
            if price_note:
                return {
                    "result": "확인요청",
                    "reason": f"금액 차이: {price_note}",
                }
        return {"result": "AI검수통과", "reason": "정상"}
    else:
        # 자동반려 대상 문제가 있으면 자동반려
        reject_problems = [p for p in problems if p in _AUTO_REJECT_PROBLEMS]
        if reject_problems:
            reason_parts = [_PROBLEM_MESSAGES.get(p, p) for p in reject_problems]
            return {"result": "자동반려", "reason": ". ".join(reason_parts)}
        reason_parts = [_PROBLEM_MESSAGES.get(p, p) for p in problems]
        return {"result": "확인요청", "reason": ". ".join(reason_parts)}


def verify_capture(drive_url: str, capture_type: str,
                   ai_instructions: str = "", campaign_info: dict | None = None) -> dict:
    """캡쳐 검증 실행

    Args:
        drive_url: Google Drive 이미지 URL
        capture_type: "purchase" 또는 "review"
        ai_instructions: 캠페인별 추가 AI 지침
        campaign_info: 캠페인 기준 정보 (상품명, 상품금액, 플랫폼 등)

    Returns:
        {
            "result": "AI검수통과" | "확인요청" | "오류",
            "reason": "사유 설명",
            "details": { ... Gemini 분석 결과 }
        }
    """
    file_id = _extract_drive_file_id(drive_url)
    if not file_id:
        return {"result": "오류", "reason": "Drive URL에서 파일ID 추출 실패", "details": {}}

    downloaded = _download_drive_image(file_id)
    if not downloaded:
        return {"result": "오류", "reason": "이미지 다운로드 실패", "details": {}}

    image_bytes, content_type = downloaded
    full_prompt = _build_prompt(capture_type, campaign_info, ai_instructions)

    analysis = _call_gemini(image_bytes, content_type, full_prompt)
    if not analysis:
        return {"result": "오류", "reason": "AI 분석 실패", "details": {}}

    judgment = _judge(analysis, capture_type)
    return {**judgment, "details": analysis}


def verify_capture_from_bytes(image_bytes: bytes, mime_type: str,
                              capture_type: str, ai_instructions: str = "",
                              campaign_info: dict | None = None) -> dict:
    """이미지 bytes에서 직접 AI 검수 (Drive 업로드 없이)

    Args:
        image_bytes: 이미지 바이트 데이터
        mime_type: 이미지 MIME type (image/jpeg, image/png 등)
        capture_type: "purchase" 또는 "review"
        ai_instructions: 캠페인별 추가 AI 지침
        campaign_info: 캠페인 기준 정보 (상품명, 상품금액, 플랫폼 등)

    Returns:
        {
            "result": "AI검수통과" | "확인요청" | "오류",
            "reason": "사유 설명",
            "details": { ... Gemini 분석 결과 },
            "parsed": { ... 파싱된 정보 (구매캡쳐일 때) }
        }
    """
    if not image_bytes:
        return {"result": "오류", "reason": "이미지 데이터 없음", "details": {}, "parsed": {}}

    full_prompt = _build_prompt(capture_type, campaign_info, ai_instructions)
    logger.info("AI 검수 요청: type=%s, campaign_info=%s, ai_instructions=%s",
                capture_type, campaign_info, ai_instructions[:200] if ai_instructions else "없음")

    analysis = _call_gemini(image_bytes, mime_type, full_prompt)
    if not analysis:
        return {"result": "오류", "reason": "AI 분석 실패", "details": {}, "parsed": {}}

    logger.info("AI 분석 결과: %s", json.dumps(analysis, ensure_ascii=False, default=str)[:500])

    # 파싱 데이터 추출 (구매캡쳐일 때)
    parsed = {}
    if capture_type == "purchase":
        # 결제금액 콤마 제거
        raw_price = str(analysis.get("결제금액", ""))
        clean_price = raw_price.replace(",", "").strip()
        parsed = {
            "주문번호": analysis.get("주문번호", ""),
            "수취인명": analysis.get("수취인명", ""),
            "수취인연락처": analysis.get("수취인연락처", ""),
            "결제금액": clean_price,
            "주소": analysis.get("주소", ""),
            "배송유형": analysis.get("배송유형", ""),
            "상품명": analysis.get("상품명", ""),
        }

    judgment = _judge(analysis, capture_type)
    logger.info("AI 판정: %s", judgment)
    return {**judgment, "details": analysis, "parsed": parsed}


def verify_capture_async(
    drive_url: str, capture_type: str, progress_id: int,
    db_manager, ai_instructions: str = "", campaign_info: dict | None = None
):
    """백그라운드 스레드로 캡쳐 검증 실행 후 DB 업데이트"""
    def _run():
        try:
            result = verify_capture(drive_url, capture_type, ai_instructions, campaign_info)
            # DB 업데이트
            col_result = "ai_purchase_result" if capture_type == "purchase" else "ai_review_result"
            col_reason = "ai_purchase_reason" if capture_type == "purchase" else "ai_review_reason"
            db_manager._execute(
                f"UPDATE progress SET {col_result} = %s, {col_reason} = %s, ai_verified_at = NOW() WHERE id = %s",
                (result["result"], result["reason"], progress_id),
            )
            logger.info(
                "AI 검수 완료: progress=%s type=%s result=%s",
                progress_id, capture_type, result["result"],
            )

            # 자동반려 처리 (로켓배송 등 심각한 문제)
            if result["result"] == "자동반려":
                _auto_reject(progress_id, capture_type, result["reason"], db_manager)
            # 양쪽 다 AI검수통과 + 리뷰제출 상태 → 자동 승인 (입금대기)
            elif result["result"] == "AI검수통과":
                _try_auto_approve(progress_id, db_manager)

        except Exception as e:
            logger.error("AI 검수 비동기 에러: %s", e, exc_info=True)

    t = threading.Thread(target=_run, daemon=True)
    t.start()


def _auto_reject(progress_id: int, capture_type: str, reason: str, db_manager):
    """AI 자동반려 처리 → 캡쳐 URL 삭제 + 상태 되돌림"""
    try:
        if capture_type == "purchase":
            db_manager._execute(
                """UPDATE progress SET purchase_capture_url = '',
                   status = '구매캡쳐대기', remark = %s,
                   created_at = NOW(), updated_at = NOW()
                   WHERE id = %s""",
                (f"AI 자동반려: {reason}", progress_id),
            )
        else:
            db_manager._execute(
                """UPDATE progress SET review_capture_url = '',
                   review_submit_date = NULL,
                   status = '리뷰대기', remark = %s, updated_at = NOW()
                   WHERE id = %s""",
                (f"AI 자동반려: {reason}", progress_id),
            )
        logger.info("AI 자동반려: progress=%s type=%s reason=%s",
                     progress_id, capture_type, reason)
    except Exception as e:
        logger.error("AI 자동반려 에러: %s", e)


def _try_auto_approve(progress_id: int, db_manager):
    """양쪽 AI검수 모두 통과 + 리뷰제출 상태이면 자동 승인"""
    try:
        row = db_manager._fetchone(
            """SELECT status, ai_purchase_result, ai_review_result
               FROM progress WHERE id = %s""",
            (progress_id,),
        )
        if not row:
            return
        # 리뷰제출 상태가 아니면 패스
        if row["status"] != "리뷰제출":
            return
        ai_p = row.get("ai_purchase_result", "") or ""
        ai_r = row.get("ai_review_result", "") or ""
        # 둘 다 통과이면 자동 승인
        if ai_p == "AI검수통과" and ai_r == "AI검수통과":
            db_manager.approve_review(progress_id)
            logger.info("AI 자동 승인: progress=%s (양쪽 통과)", progress_id)
    except Exception as e:
        logger.error("AI 자동 승인 에러: %s", e)

"""
capture_verifier.py - AI 캡쳐 검수 (Gemini Vision)

구매캡쳐/리뷰캡쳐 업로드 시 자동 검증.
Google Drive 이미지를 다운로드 → Gemini 2.0 Flash로 분석.
"""

import io
import json
import logging
import re
import threading

import requests

logger = logging.getLogger(__name__)

# Gemini API
_GEMINI_API_KEY = "AIzaSyDDpgXPLCq-ZfOPq9tgyScD6Y1pBMC9Cf4"
_GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-2.0-flash:generateContent?key={_GEMINI_API_KEY}"
)

# ── 기본 프롬프트 ──

PURCHASE_PROMPT = """이 이미지는 온라인 쇼핑몰(쿠팡/네이버/카카오 등)의 주문 상세 캡쳐입니다.
다음 정보를 추출해주세요. 이미지에서 보이지 않거나 잘린 항목은 빈 문자열("")로 응답하세요.

JSON 형식으로만 응답:
{
    "주문번호": "",
    "상품명": "",
    "수량": "",
    "결제금액": "",
    "수취인명": "",
    "배송유형": "",
    "문제점": []
}

배송유형은 다음 중 하나로 판별:
- "로켓배송": 쿠팡 로켓배송 마크가 있는 경우
- "로켓와우": 로켓와우 배송
- "판매자배송": 판매자가 직접 배송
- "판매자로켓": 판매자로켓 표시가 있는 경우
- "확인불가": 배송유형을 알 수 없는 경우

문제점에는 다음 중 해당하는 것을 배열로 넣어주세요:
- "상품정보_미확인": 상품명이나 이미지가 잘려서 뭘 구매했는지 확인 불가
- "주문번호_미확인": 주문번호가 보이지 않음
- "수취인_미확인": 수취인명이 보이지 않음
- "정상": 모든 정보가 정상적으로 확인됨

문제점이 없으면 ["정상"]으로 응답하세요."""

REVIEW_PROMPT = """이 이미지는 온라인 쇼핑몰에 작성된 리뷰 캡쳐입니다.
다음 정보를 확인해주세요.

JSON 형식으로만 응답:
{
    "리뷰내용_확인": true/false,
    "사진포함": true/false,
    "별점": "",
    "문제점": []
}

문제점에는 다음 중 해당하는 것을 배열로 넣어주세요:
- "리뷰내용_미확인": 리뷰 텍스트가 보이지 않거나 잘림
- "리뷰_아님": 이 이미지가 리뷰 캡쳐가 아닌 것으로 보임
- "정상": 리뷰가 정상적으로 작성되어 있음

문제점이 없으면 ["정상"]으로 응답하세요."""


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

    try:
        resp = requests.post(_GEMINI_URL, json=payload, timeout=60)
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


def verify_capture(drive_url: str, capture_type: str, ai_instructions: str = "") -> dict:
    """캡쳐 검증 실행

    Args:
        drive_url: Google Drive 이미지 URL
        capture_type: "purchase" 또는 "review"
        ai_instructions: 캠페인별 추가 AI 지침

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

    # 프롬프트 구성
    base_prompt = PURCHASE_PROMPT if capture_type == "purchase" else REVIEW_PROMPT
    if ai_instructions:
        full_prompt = f"{base_prompt}\n\n추가 검수 지침:\n{ai_instructions}"
    else:
        full_prompt = base_prompt

    analysis = _call_gemini(image_bytes, content_type, full_prompt)
    if not analysis:
        return {"result": "오류", "reason": "AI 분석 실패", "details": {}}

    # 판정
    problems = analysis.get("문제점", [])
    if not problems or problems == ["정상"]:
        # 구매캡쳐: 배송유형 체크
        if capture_type == "purchase":
            delivery = analysis.get("배송유형", "")
            if delivery in ("로켓배송", "로켓와우"):
                return {
                    "result": "확인요청",
                    "reason": f"배송유형이 '{delivery}'입니다. 판매자배송으로 구매해야 합니다.",
                    "details": analysis,
                }
        return {"result": "AI검수통과", "reason": "정상", "details": analysis}
    else:
        reason_parts = []
        for p in problems:
            if p == "상품정보_미확인":
                reason_parts.append("상품 정보가 확인되지 않습니다")
            elif p == "주문번호_미확인":
                reason_parts.append("주문번호가 보이지 않습니다")
            elif p == "수취인_미확인":
                reason_parts.append("수취인 정보가 보이지 않습니다")
            elif p == "리뷰내용_미확인":
                reason_parts.append("리뷰 내용이 확인되지 않습니다")
            elif p == "리뷰_아님":
                reason_parts.append("리뷰 캡쳐가 아닌 것으로 보입니다")
            else:
                reason_parts.append(p)
        return {
            "result": "확인요청",
            "reason": ". ".join(reason_parts),
            "details": analysis,
        }


def verify_capture_async(
    drive_url: str, capture_type: str, progress_id: int,
    db_manager, ai_instructions: str = ""
):
    """백그라운드 스레드로 캡쳐 검증 실행 후 DB 업데이트"""
    def _run():
        try:
            result = verify_capture(drive_url, capture_type, ai_instructions)
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
        except Exception as e:
            logger.error("AI 검수 비동기 에러: %s", e, exc_info=True)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

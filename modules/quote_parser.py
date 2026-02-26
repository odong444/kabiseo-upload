"""
quote_parser.py - 업체 요청서 텍스트 자동 파싱

업체에서 보내는 자유형식 텍스트를 구조화된 dict로 변환합니다.
▶ 기호를 기준으로 key-value를 추출하고, 누락된 필드는 빈값으로 채웁니다.
"""

import re
import logging

logger = logging.getLogger(__name__)


def parse_request_text(raw_text: str) -> dict:
    """요청서 텍스트를 파싱하여 구조화된 dict 반환.

    Returns:
        {
            "product_link": "",
            "same_day_ship": "",
            "ship_deadline": "",
            "entry_method": "",        # 링크유입 / 키워드유입
            "keyword": "",
            "keyword_position": "",
            "current_rank": "",
            "options": "",
            "total_qty": 0,
            "daily_qty": 0,
            "courier": "",
            "use_3pl": False,
            "cost_3pl": 0,
            "weekend_work": False,
            "review_provided": True,
            "review_qty": 0,
            "review_text_provided": False,
            "review_text_deadline": "",
            "memo": "",
        }
    """
    result = {
        "product_link": "",
        "same_day_ship": "",
        "ship_deadline": "",
        "entry_method": "",
        "keyword": "",
        "keyword_position": "",
        "current_rank": "",
        "options": "",
        "total_qty": 0,
        "daily_qty": 0,
        "courier": "",
        "use_3pl": False,
        "cost_3pl": 0,
        "weekend_work": False,
        "review_provided": True,
        "review_qty": 0,
        "review_text_provided": False,
        "review_text_deadline": "",
        "memo": "",
    }

    if not raw_text or not raw_text.strip():
        return result

    text = raw_text.strip()
    lines = text.split("\n")

    # 전체 텍스트를 하나의 문자열로 합쳐서 패턴 매칭 (줄바꿈 포함)
    full = "\n".join(lines)

    # ── 상품링크 ──
    link_match = re.search(r"https?://[^\s]+", full)
    if link_match:
        result["product_link"] = link_match.group(0).strip()

    # ── 당일발송 ──
    for line in lines:
        low = line.strip().lower()
        if "당일발송" in line and ("제품" in line or "상품" in line or "?" in line):
            # "▶당일발송(오늘출발) 제품인가요? 네"
            if _has_yes(line):
                result["same_day_ship"] = "Y"
            elif _has_no(line):
                result["same_day_ship"] = "N"
        elif "당일발송" in line and "마감" in line:
            # "당일발송 마감시간: 오후 6시 30분"
            time_part = _extract_after_colon(line)
            if time_part:
                result["ship_deadline"] = time_part

    # ── 유입방식 ──
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.search(r"1\.\s*링크\s*유입", stripped):
            after = _extract_after_colon(stripped)
            if after and ("http" in after or after.strip()):
                result["entry_method"] = "링크유입"
                if not after.strip():
                    result["entry_method"] = ""
        if re.search(r"2\.\s*키워드\s*유입", stripped):
            kw = _extract_after_colon(stripped)
            if kw:
                result["entry_method"] = "키워드유입"
                result["keyword"] = kw
        if re.search(r"2-1\.", stripped) and ("노출" in stripped or "순위" in stripped):
            rank = _extract_after_colon(stripped)
            if rank:
                result["current_rank"] = rank

    # 유입방식 결정: 키워드가 있으면 키워드유입, 아니면 링크유입
    if not result["entry_method"]:
        if result["keyword"]:
            result["entry_method"] = "키워드유입"
        elif result["product_link"]:
            result["entry_method"] = "링크유입"

    # ── 상품옵션 ──
    for line in lines:
        if re.search(r"상품\s*선택\s*옵션|옵션.*3개", line, re.IGNORECASE):
            opt = _extract_after_colon(line)
            if opt:
                result["options"] = opt

    # ── 총 구매수량 ──
    for line in lines:
        if re.search(r"총\s*구매\s*수량", line):
            qty = _extract_number(line)
            if qty:
                result["total_qty"] = qty

    # ── 일 구매수량 ──
    for line in lines:
        if re.search(r"일\s*구매\s*수량", line):
            qty = _extract_number(line)
            if qty:
                result["daily_qty"] = qty

    # ── 택배사 ──
    for line in lines:
        if re.search(r"출고\s*택배사|택배사", line):
            courier = _extract_after_colon(line)
            if courier:
                result["courier"] = courier

    # ── 3PL ──
    for line in lines:
        if "3pl" in line.lower() or "배송대행" in line:
            val = _extract_after_colon(line)
            if val:
                if _has_no(val) or val.strip().upper() == "X":
                    result["use_3pl"] = False
                else:
                    result["use_3pl"] = True
                    cost = _extract_number(val)
                    if cost:
                        result["cost_3pl"] = cost

    # ── 주말 작업 ──
    for line in lines:
        if "주말" in line and ("작업" in line or "유무" in line):
            val = _extract_after_colon(line)
            if val:
                if "유" in val and "무" not in val:
                    result["weekend_work"] = True
                elif _has_yes(val):
                    result["weekend_work"] = True

    # ── 리뷰 제공 ──
    for line in lines:
        if re.search(r"리뷰\s*사용|저희측\s*리뷰", line):
            val = _extract_after_colon(line)
            if val:
                if "부탁" in val or "사용" in val or _has_yes(val):
                    result["review_provided"] = True
                elif "전달" in val or "직접" in val:
                    result["review_provided"] = False

    # ── 리뷰 원고 수 ──
    for line in lines:
        if "원고" in line and ("몇" in line or "건" in line):
            qty = _extract_number(line)
            if qty:
                result["review_qty"] = qty
        # 다음 줄에 숫자만 있는 경우 처리
    for i, line in enumerate(lines):
        if "원고" in line and ("몇" in line or "건" in line):
            # 같은 줄에서 추출 실패 시 다음 줄 확인
            if not result["review_qty"] and i + 1 < len(lines):
                next_qty = _extract_number(lines[i + 1])
                if next_qty:
                    result["review_qty"] = next_qty

    # ── 텍스트/사진 전달 ──
    for line in lines:
        if ("텍스트" in line or "사진" in line) and ("주" in line or "전달" in line):
            val = _extract_after_colon(line)
            if val:
                if _has_no(val) or "안드" in val or "없" in val:
                    result["review_text_provided"] = False
                else:
                    result["review_text_provided"] = True
                    result["review_text_deadline"] = val

    return result


def _extract_after_colon(line: str) -> str:
    """콜론(:) 뒤의 값 추출"""
    if ":" in line:
        parts = line.split(":", 1)
        return parts[1].strip()
    if "：" in line:  # 전각 콜론
        parts = line.split("：", 1)
        return parts[1].strip()
    return ""


def _extract_number(text: str) -> int:
    """텍스트에서 첫 번째 숫자 추출"""
    # 콜론 뒤 우선
    after = _extract_after_colon(text)
    target = after if after else text
    match = re.search(r"(\d+)", target)
    if match:
        return int(match.group(1))
    return 0


def _has_yes(text: str) -> bool:
    """긍정 응답 확인"""
    t = text.strip().lower()
    return bool(re.search(r"네|예|yes|y$|유$|o$|사용|합니다|부탁", t))


def _has_no(text: str) -> bool:
    """부정 응답 확인"""
    t = text.strip().lower()
    return bool(re.search(r"아니|no|n$|무$|x$|없|안[드합]", t))

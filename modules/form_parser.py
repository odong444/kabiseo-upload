"""
form_parser.py - 리뷰어 양식 파싱

채팅 메시지에서 아이디, 이름, 연락처 등을 추출.
regex 우선 → 실패 시 AI 폴백 (선택).
"""

import re
import logging

logger = logging.getLogger(__name__)


def parse_identity(text: str) -> dict:
    """이름 + 연락처 파싱

    Returns: {"name": str, "phone": str} or empty dict
    """
    result = {}

    # 이름 패턴
    name_patterns = [
        r"이름\s*[:：]\s*(.+?)(?:\s|$|/|,)",
        r"이름\s*[:：]\s*(.+)",
    ]
    for p in name_patterns:
        m = re.search(p, text)
        if m:
            result["name"] = m.group(1).strip()
            break

    # 연락처 패턴
    phone_patterns = [
        r"연락처\s*[:：]\s*([\d\-]+)",
        r"(010[\-\s]?\d{4}[\-\s]?\d{4})",
    ]
    for p in phone_patterns:
        m = re.search(p, text)
        if m:
            digits = re.sub(r"[^0-9]", "", m.group(1))
            if len(digits) == 11:
                result["phone"] = f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
            break

    return result


def parse_form(text: str) -> dict:
    """양식 파싱 (아이디 등)

    Returns: {"아이디": str, ...} or empty dict
    """
    result = {}

    # 아이디 패턴
    id_patterns = [
        r"아이디\s*[:：]\s*(\S+)",
        r"[Ii][Dd]\s*[:：]\s*(\S+)",
    ]
    for p in id_patterns:
        m = re.search(p, text)
        if m:
            result["아이디"] = m.group(1).strip()
            break

    # 예금주 패턴
    depositor_patterns = [
        r"예금주\s*[:：]\s*(.+?)(?:\s|$|/|,)",
        r"예금주\s*[:：]\s*(.+)",
    ]
    for p in depositor_patterns:
        m = re.search(p, text)
        if m:
            result["예금주"] = m.group(1).strip()
            break

    return result


def parse_menu_choice(text: str) -> int | None:
    """메뉴 번호 파싱 (1~5)"""
    text = text.strip()

    # 직접 번호
    if text in ("1", "2", "3", "4", "5"):
        return int(text)

    # 번호 + 번 패턴
    m = re.match(r"^(\d)\s*번?$", text)
    if m:
        num = int(m.group(1))
        if 1 <= num <= 5:
            return num

    # 키워드 매칭
    keywords = {
        1: ["신청", "체험단", "모집"],
        2: ["진행", "현황", "상황"],
        3: ["사진", "캡쳐", "제출", "업로드"],
        4: ["입금", "정산", "돈"],
        5: ["문의", "기타", "질문"],
    }
    for num, kws in keywords.items():
        for kw in kws:
            if kw in text:
                return num

    return None


def parse_campaign_choice(text: str) -> int | None:
    """캠페인 번호 선택 파싱"""
    text = text.strip()
    m = re.match(r"^(\d+)\s*번?$", text)
    if m:
        return int(m.group(1))
    try:
        return int(text)
    except ValueError:
        return None

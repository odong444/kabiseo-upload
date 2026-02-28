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


# 전체 양식 필드 파싱 대상
FORM_FIELDS = [
    ("아이디", [r"아이디\s*[:：]\s*(\S+)", r"[Ii][Dd]\s*[:：]\s*(\S+)"]),
    ("수취인명", [r"수취인명?\s*[:：]\s*(.+?)(?:\n|$)", r"수취인\s*[:：]\s*(.+?)(?:\n|$)", r"이름\s*[:：]\s*(.+?)(?:\n|$)"]),
    ("연락처", [r"연락처\s*[:：]\s*([\d\-]+)", r"전화번호?\s*[:：]\s*([\d\-]+)", r"(010[\-\s]?\d{4}[\-\s]?\d{4})"]),
    ("은행", [r"은행\s*[:：]\s*(.+?)(?:\n|$|/|,)"]),
    ("계좌", [r"계좌\s*(?:번호)?\s*[:：]\s*([\d\-]+)"]),
    ("예금주", [r"예금주\s*[:：]\s*(.+?)(?:\n|$|/|,)"]),
    ("주소", [r"주소\s*[:：]\s*(.+?)(?:\n|$)"]),
    ("주문번호", [r"주문번호\s*[:：]\s*(\S+)", r"주문\s*번호\s*[:：]\s*(\S+)"]),
    ("결제금액", [r"결제금액\s*[:：]\s*([\d,]+)"]),
]


def parse_full_form(text: str) -> dict:
    """단일 양식 파싱 (아이디, 수취인명, 연락처, 은행, 계좌, 예금주, 주소)

    Returns: dict of parsed fields
    """
    result = {}

    for field_name, patterns in FORM_FIELDS:
        for p in patterns:
            m = re.search(p, text, re.MULTILINE)
            if m:
                val = m.group(1).strip()
                if val:
                    result[field_name] = val
                break

    return result


def parse_multiple_forms(text: str) -> list[dict]:
    """한 메시지에서 여러 양식 분리 파싱

    아이디 필드가 2개 이상이면 아이디 기준으로 분할.
    1개 이하면 단일 양식으로 처리.

    Returns: list of parsed form dicts
    """
    # 아이디 필드 위치 찾기
    id_matches = list(re.finditer(r"(?:아이디|[Ii][Dd])\s*[:：]", text))

    if len(id_matches) <= 1:
        result = parse_full_form(text)
        return [result] if result else []

    # 아이디 필드 위치 기준으로 텍스트 분할
    forms = []
    for i, match in enumerate(id_matches):
        start = match.start()
        end = id_matches[i + 1].start() if i + 1 < len(id_matches) else len(text)
        section = text[start:end].strip()
        parsed = parse_full_form(section)
        if parsed:
            forms.append(parsed)

    return forms


def count_form_fields(parsed: dict) -> int:
    """파싱된 양식 필드 수 (필수 필드 기준)"""
    required = ["수취인명", "연락처", "은행", "계좌", "예금주", "주소", "주문번호", "결제금액"]
    return sum(1 for f in required if parsed.get(f))


def parse_menu_choice(text: str) -> int | None:
    """메뉴 번호 파싱 (1~6)"""
    text = text.strip()

    # 직접 번호
    if text in ("1", "2", "3", "4", "5", "6"):
        return int(text)

    # 번호 + 번 패턴
    m = re.match(r"^(\d)\s*번?$", text)
    if m:
        num = int(m.group(1))
        if 1 <= num <= 6:
            return num

    # 텍스트 대화는 모두 AI로 → 키워드 매칭 제거
    # 버튼 클릭만 숫자로 전달되므로 위의 번호 매칭만 사용
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

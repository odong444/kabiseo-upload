"""
utils.py - 공통 유틸리티 함수
"""

import re
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))


def now_kst():
    """현재 KST 시각"""
    return datetime.now(KST)


def today_str():
    """오늘 날짜 문자열 (YYYY-MM-DD)"""
    return now_kst().strftime("%Y-%m-%d")


def normalize_phone(phone: str) -> str:
    """연락처 정규화: 010-XXXX-XXXX 형식"""
    digits = re.sub(r"[^0-9]", "", phone)
    if len(digits) == 11 and digits.startswith("010"):
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    return phone.strip()


def validate_phone(phone: str) -> bool:
    """010-XXXX-XXXX 형식 검증"""
    return bool(re.match(r"^010-\d{4}-\d{4}$", normalize_phone(phone)))


def safe_int(val, default=0):
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def truncate(text: str, max_len: int = 100) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."

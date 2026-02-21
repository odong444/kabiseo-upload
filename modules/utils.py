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


def parse_buy_time(text: str):
    """구매가능시간 파싱

    지원 형식:
      "09:00~22:00"         → 같은 날
      "18:00~익일07:00"     → 자정 넘김
      공백/구분자 허용: "09:00 ~ 22:00", "18:00 ~ 익일 07:00"

    Returns: (start_hour, start_min, end_hour, end_min, next_day) or None
    """
    if not text or not text.strip():
        return None

    text = text.strip().replace(" ", "")

    # "익일" 포함 여부
    next_day = "익일" in text
    text = text.replace("익일", "")

    # HH:MM~HH:MM 패턴
    m = re.match(r"(\d{1,2}):(\d{2})[~\-](\d{1,2}):(\d{2})", text)
    if not m:
        return None

    sh, sm, eh, em = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))

    # 시작 > 종료이면 자동으로 익일 판단
    if not next_day and sh > eh:
        next_day = True

    return (sh, sm, eh, em, next_day)


def is_within_buy_time(buy_time_str: str) -> bool:
    """현재 KST 시각이 구매가능시간 범위 내인지

    빈 문자열 → True (24시간)
    파싱 실패 → True (안전하게 표시)
    """
    if not buy_time_str or not buy_time_str.strip():
        return True

    parsed = parse_buy_time(buy_time_str)
    if not parsed:
        return True  # 파싱 실패 시 안전하게 표시

    sh, sm, eh, em, next_day = parsed
    now = now_kst()
    now_minutes = now.hour * 60 + now.minute
    start_minutes = sh * 60 + sm
    end_minutes = eh * 60 + em

    if next_day:
        # 자정 넘김: 18:00~07:00 → now >= 18:00 OR now <= 07:00
        return now_minutes >= start_minutes or now_minutes <= end_minutes
    else:
        # 같은 날: 09:00~22:00
        return start_minutes <= now_minutes <= end_minutes

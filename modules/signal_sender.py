"""
signal_sender.py - 서버PC 태스크 API 호출 유틸리티

Railway → 서버PC(222.122.194.202:5050)로 태스크를 전송합니다.
친구추가, 독촉, 안내 메시지 등을 서버PC의 TaskQueue에 등록합니다.

실패해도 예외를 삼키고 로깅만 합니다 (리뷰어 신청 흐름에 영향 없음).
"""

import os
import re
import logging
import requests

logger = logging.getLogger("signal_sender")

TASK_API_URL = os.environ.get("TASK_API_URL", "http://222.122.194.202:5050")
TASK_API_KEY = os.environ.get("TASK_API_KEY", "_fNmY5SeHyigMgkR5LIngpxBB1gDoZLF")


def _format_phone(phone: str) -> str:
    """전화번호를 010-XXXX-XXXX 형식으로 통일."""
    digits = re.sub(r"[^0-9]", "", phone)
    if len(digits) == 11 and digits.startswith("010"):
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    if len(digits) == 10 and digits.startswith("01"):
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return phone


def send_task(task_type: str, data: dict, priority: int = 2) -> bool:
    """서버PC에 태스크 전송. 성공 시 True, 실패 시 False."""
    if not TASK_API_URL:
        logger.warning("TASK_API_URL 미설정, 태스크 전송 스킵")
        return False

    try:
        r = requests.post(
            f"{TASK_API_URL}/api/task/submit",
            json={"type": task_type, "data": data, "priority": priority},
            headers={
                "X-API-Key": TASK_API_KEY,
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        result = r.json()
        if result.get("ok"):
            task_id = result.get("task_id")
            logger.info("태스크 전송 성공: type=%s task_id=%s", task_type, task_id)
            return True
        else:
            logger.warning("태스크 전송 거부: %s", result.get("error"))
            return False
    except Exception as e:
        logger.warning("태스크 전송 실패 (서버PC 연결 불가): %s", e)
        return False


def request_friend_add(name: str, phone: str) -> bool:
    """친구추가 요청 (priority=1 긴급)."""
    phone = _format_phone(phone)
    return send_task("friend_add", {"name": name, "phone": phone}, priority=1)


def request_reminder(name: str, phone: str, message: str) -> bool:
    """독촉 메시지 요청 (priority=1 긴급)."""
    phone = _format_phone(phone)
    friend_name = f"{name} {phone}"
    return send_task("reminder", {"name": friend_name, "message": message}, priority=1)


def request_notification(name: str, phone: str, message: str) -> bool:
    """안내 메시지 요청 (priority=2 일반)."""
    phone = _format_phone(phone)
    friend_name = f"{name} {phone}"
    return send_task("notification", {"name": friend_name, "message": message}, priority=2)


def cancel_campaign_tasks(campaign_id: str) -> int:
    """서버PC의 해당 캠페인 대기 홍보 태스크 일괄 취소. 취소된 건수 반환."""
    try:
        r = requests.post(
            f"{TASK_API_URL}/api/task/cancel-campaign",
            json={"campaign_id": campaign_id},
            headers={"X-API-Key": TASK_API_KEY},
            timeout=5,
        )
        if r.ok:
            count = r.json().get("cancelled", 0)
            if count:
                logger.info("캠페인 [%s] 홍보 태스크 %d건 취소", campaign_id, count)
            return count
    except Exception as e:
        logger.warning("캠페인 태스크 취소 요청 실패: %s", e)
    return 0

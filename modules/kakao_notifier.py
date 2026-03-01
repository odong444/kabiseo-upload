"""
kakao_notifier.py - 카카오톡 알림 발송 디스패처

상태 전환, 독촉, 타임아웃 등 카카오톡 메시지를 발송합니다.
signal_sender를 통해 서버PC에 태스크를 전송합니다.
"""

import logging
from datetime import timedelta
from modules.utils import now_kst

from modules import kakao_templates as ktpl
from modules.signal_sender import request_notification, request_reminder

logger = logging.getLogger("kakao_notifier")

FOOTER = "\n\n※ 본 메시지는 발신전용입니다. 문의사항은 웹채팅을 이용해주세요."


class KakaoNotifier:
    """카카오톡 알림 발송기"""

    def __init__(self, db_manager, web_url: str = ""):
        self.db = db_manager
        self.web_url = web_url

    def _add_footer(self, msg: str) -> str:
        return msg + FOOTER

    def _get_progress_info(self, progress_id: int) -> dict:
        """progress 행에서 발송에 필요한 정보 추출"""
        row = self.db.get_row_dict(progress_id)
        if not row:
            return {}
        return {
            "name": row.get("진행자이름", "") or row.get("수취인명", ""),
            "phone": row.get("진행자연락처", "") or row.get("연락처", ""),
            "recipient_name": row.get("수취인명", ""),
            "product_name": row.get("제품명", ""),
            "store_ids": row.get("아이디", ""),
            "deadline": row.get("리뷰기한", ""),
            "amount": row.get("입금금액", "0"),
            "status": row.get("상태", ""),
        }

    # ──────── 상태별 알림 (수동 발송용) ────────

    def notify_status(self, progress_id: int, status: str, extra: dict = None) -> bool:
        """상태에 맞는 템플릿으로 카톡 발송"""
        info = self._get_progress_info(progress_id)
        if not info.get("name") or not info.get("phone"):
            return False

        template = ktpl.STATUS_TEMPLATES.get(status)
        if not template:
            logger.warning("템플릿 없는 상태: %s", status)
            return False

        extra = extra or {}
        msg = template.format(
            name=info["name"],
            recipient_name=info["recipient_name"],
            product_name=info["product_name"],
            store_ids=info["store_ids"],
            deadline=info.get("deadline", ""),
            amount=extra.get("amount", info.get("amount", "0")),
            web_url=self.web_url,
        )
        return request_notification(info["name"], info["phone"], self._add_footer(msg))

    # ──────── 자동 발송: 리뷰 반려 ────────

    def notify_review_rejected(self, progress_id: int, reason: str) -> bool:
        """리뷰 반려 알림 (자동)"""
        info = self._get_progress_info(progress_id)
        if not info.get("name") or not info.get("phone"):
            return False

        msg = ktpl.REVIEW_REJECTED.format(
            recipient_name=info["recipient_name"],
            product_name=info["product_name"],
            store_ids=info["store_ids"],
            reason=reason,
            web_url=self.web_url,
        )
        return request_notification(info["name"], info["phone"], self._add_footer(msg))

    # ──────── 자동 발송: 타임아웃 ────────

    def notify_timeout_warning(self, name: str, phone: str, product_name: str,
                               recipient_name: str = "", store_ids: str = "") -> bool:
        """타임아웃 15분 경고"""
        msg = ktpl.TIMEOUT_WARNING.format(
            product_name=product_name,
            recipient_name=recipient_name,
            store_ids=store_ids,
            web_url=self.web_url,
        )
        return request_reminder(name, phone, self._add_footer(msg))

    def notify_timeout_cancelled(self, name: str, phone: str, product_name: str,
                                  recipient_name: str = "", store_ids: str = "") -> bool:
        """타임아웃 취소"""
        msg = ktpl.TIMEOUT_CANCELLED.format(
            product_name=product_name,
            recipient_name=recipient_name,
            store_ids=store_ids,
            web_url=self.web_url,
        )
        return request_notification(name, phone, self._add_footer(msg))

    # ──────── 관리자 수동 독촉 ────────

    def send_reminder(self, progress_id: int, custom_message: str = "") -> bool:
        """관리자 수동 독촉 — 상태별 기본 메시지 + 링크"""
        info = self._get_progress_info(progress_id)
        if not info.get("name") or not info.get("phone"):
            return False

        status = info["status"]
        defaults = ktpl.DEFAULT_REMINDERS.get(status, {
            "message": "진행 상황을 확인해주세요.",
            "path": "/chat",
        })

        message = custom_message or defaults["message"]
        link = f"{self.web_url}{defaults['path']}"

        msg = ktpl.ADMIN_REMINDER.format(
            message=message,
            recipient_name=info["recipient_name"],
            product_name=info["product_name"],
            store_ids=info["store_ids"],
            link=link,
        )
        return request_reminder(info["name"], info["phone"], self._add_footer(msg))

    # ──────── 문의 답변 카톡 ────────

    def notify_inquiry_reply(self, name: str, phone: str, reply: str) -> bool:
        """문의 답변을 리뷰어에게 카톡 발송"""
        msg = ktpl.INQUIRY_REPLY.format(
            reply=reply,
            web_url=self.web_url,
        )
        return request_notification(name, phone, self._add_footer(msg))

    def notify_admin_urgent_inquiry(self, admin_name: str, admin_phone: str,
                                     reviewer_name: str, reviewer_phone: str,
                                     message: str) -> bool:
        """긴급 문의 → 관리자에게 카톡 알림"""
        preview = message[:100] + ("..." if len(message) > 100 else "")
        msg = ktpl.ADMIN_URGENT_INQUIRY.format(
            name=reviewer_name,
            phone=reviewer_phone,
            message=preview,
        )
        return request_reminder(admin_name, admin_phone, msg)

    def notify_admin_inquiry(self, admin_name: str, admin_phone: str,
                              reviewer_name: str, reviewer_phone: str,
                              message: str) -> bool:
        """일반 문의 → 관리자에게 카톡 알림"""
        preview = message[:100] + ("..." if len(message) > 100 else "")
        msg = ktpl.ADMIN_INQUIRY.format(
            name=reviewer_name,
            phone=reviewer_phone,
            message=preview,
        )
        return request_notification(admin_name, admin_phone, msg)

    # ──────── Drive 업로드 실패 → 관리자 알림 ────────

    def notify_admin_upload_failure(self, progress_id: int, capture_type: str,
                                     filename: str, error: str):
        """Drive 업로드 최종 실패 → 활성 담당자에게 카톡 알림"""
        label = "구매캡쳐" if capture_type == "purchase" else "리뷰캡쳐"
        msg = ktpl.ADMIN_UPLOAD_FAILURE.format(
            progress_id=progress_id,
            capture_type=label,
            filename=filename,
            error=str(error)[:100],
        )
        managers = self.db.get_active_managers()
        if not managers:
            managers = [{"name": "오동열", "phone": "010-7210-0210"}]
        for mgr in managers:
            try:
                request_reminder(mgr["name"], mgr["phone"], msg)
            except Exception as e:
                logger.warning("업로드 실패 알림 발송 에러: %s", e)

    # ──────── 리뷰 기한 리마인더 (스케줄러) ────────

    def send_review_deadline_reminders(self) -> int:
        """D-3, D-1 리뷰 기한 리마인더. 발송 건수 반환."""
        today = now_kst().date()
        d3 = today + timedelta(days=3)
        d1 = today + timedelta(days=1)

        rows = self.db._fetchall(
            """SELECT p.id, p.store_id, p.recipient_name, p.review_deadline,
                      p.last_reminder_date, r.name, r.phone,
                      COALESCE(NULLIF(c.campaign_name, ''), c.product_name) AS product_name
               FROM progress p
               JOIN reviewers r ON p.reviewer_id = r.id
               JOIN campaigns c ON p.campaign_id = c.id
               WHERE p.status = '리뷰대기'
               AND p.review_deadline IS NOT NULL
               AND p.review_deadline IN (%s, %s)
               AND (p.last_reminder_date IS NULL OR p.last_reminder_date < %s)""",
            (d3, d1, today)
        )

        sent = 0
        for row in rows:
            deadline = row["review_deadline"]
            days_left = (deadline - today).days

            msg = ktpl.REVIEW_DEADLINE_REMINDER.format(
                days=days_left,
                recipient_name=row.get("recipient_name", ""),
                product_name=row.get("product_name", ""),
                store_ids=row.get("store_id", ""),
                web_url=self.web_url,
            )

            ok = request_notification(row["name"], row["phone"], self._add_footer(msg))
            if ok:
                self.db._execute(
                    "UPDATE progress SET last_reminder_date = %s WHERE id = %s",
                    (today, row["id"])
                )
                sent += 1

        return sent

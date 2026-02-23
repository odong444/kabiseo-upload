"""
kakao_templates.py - 카카오톡 알림 메시지 템플릿

상태 전환, 독촉, 타임아웃 등 카카오톡으로 발송되는 메시지 정의.
{web_url}, {product_name} 등은 발송 시 동적 치환됩니다.
"""

# ──────── 상태별 알림 ────────

GUIDE_SENT = """[카비서] 구매 가이드를 확인해주세요.

상품: {product_name}
수취인: {recipient_name} / 아이디: {store_ids}

가이드와 양식을 확인하고 제출해주세요.
👉 {web_url}/chat"""

PURCHASE_WAIT = """[카비서] 구매 캡쳐를 제출해주세요.

상품: {product_name}
수취인: {recipient_name} / 아이디: {store_ids}

👉 {web_url}/upload/purchase

⚠️ 미제출 시 자동 취소됩니다."""

REVIEW_WAIT = """[카비서] 리뷰를 작성해주세요.

상품: {product_name}
수취인: {recipient_name} / 아이디: {store_ids}
리뷰 기한: {deadline}

👉 {web_url}/upload/review"""

REVIEW_SUBMITTED = """[카비서] 리뷰 캡쳐가 접수되었습니다.

상품: {product_name}
수취인: {recipient_name} / 아이디: {store_ids}

검수 후 입금 안내드리겠습니다.
진행현황: {web_url}/status"""

REVIEW_REJECTED = """[카비서] 리뷰 검수 반려

상품: {product_name}
수취인: {recipient_name} / 아이디: {store_ids}
사유: {reason}

리뷰를 다시 제출해주세요.
👉 {web_url}/upload/review"""

PAYMENT_WAIT = """[카비서] 리뷰 검수 승인!

상품: {product_name}
수취인: {recipient_name} / 아이디: {store_ids}

곧 리뷰비를 입금해드리겠습니다.
입금현황: {web_url}/payment"""

SETTLED = """[카비서] 입금 완료!

상품: {product_name}
수취인: {recipient_name} / 아이디: {store_ids}
금액: {amount}원

이용해주셔서 감사합니다.
입금현황: {web_url}/payment"""

# ──────── 타임아웃 ────────

TIMEOUT_WARNING = """[카비서] 5분 후 자동 취소됩니다!

상품: {product_name}
수취인: {recipient_name} / 아이디: {store_ids}

양식과 캡쳐를 빨리 제출해주세요.
👉 {web_url}/chat"""

TIMEOUT_CANCELLED = """[카비서] 신청이 자동 취소되었습니다.

상품: {product_name}
수취인: {recipient_name} / 아이디: {store_ids}

자리가 있으면 다시 신청 가능합니다.
👉 {web_url}/chat"""

# ──────── 리뷰 기한 리마인더 ────────

REVIEW_DEADLINE_REMINDER = """[카비서] 리뷰 기한이 {days}일 남았습니다.

상품: {product_name}
수취인: {recipient_name} / 아이디: {store_ids}

리뷰 캡쳐를 제출해주세요.
👉 {web_url}/upload/review"""

# ──────── 관리자 수동 독촉 ────────

ADMIN_REMINDER = """[카비서] {message}

상품: {product_name}
수취인: {recipient_name} / 아이디: {store_ids}

👉 {link}"""

# ──────── 상태별 기본 독촉 메시지 + 링크 ────────

DEFAULT_REMINDERS = {
    "가이드전달": {
        "message": "구매 가이드를 확인하고 양식을 제출해주세요.",
        "path": "/chat",
    },
    "구매캡쳐대기": {
        "message": "구매 캡쳐를 제출해주세요.",
        "path": "/upload/purchase",
    },
    "리뷰대기": {
        "message": "리뷰 캡쳐를 제출해주세요.",
        "path": "/upload/review",
    },
    "리뷰제출": {
        "message": "검수 진행 중입니다. 잠시만 기다려주세요.",
        "path": "/status",
    },
    "입금대기": {
        "message": "곧 입금 예정입니다.",
        "path": "/payment",
    },
}

# ──────── 상태 → 템플릿 매핑 ────────

# ──────── 문의 답변 ────────

INQUIRY_REPLY = """[카비서] 문의 답변이 도착했습니다.

{reply}

👉 {web_url}/chat"""

# ──────── 긴급 문의 → 관리자 알림 ────────

ADMIN_URGENT_INQUIRY = """[카비서 긴급] 새 문의 접수

이름: {name}
연락처: {phone}
내용: {message}"""

# ──────── 상태 → 템플릿 매핑 ────────

STATUS_TEMPLATES = {
    "가이드전달": GUIDE_SENT,
    "구매캡쳐대기": PURCHASE_WAIT,
    "리뷰대기": REVIEW_WAIT,
    "리뷰제출": REVIEW_SUBMITTED,
    "입금대기": PAYMENT_WAIT,
    "입금완료": SETTLED,
}

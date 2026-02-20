"""
response_templates.py - 카비서 응답 템플릿
"""


# ──────────── STEP 0: 인사 / 메뉴 ────────────

WELCOME_NEW = """안녕하세요! 카비서입니다 😊
리뷰 체험단 진행을 도와드리겠습니다.

아래에서 원하시는 항목을 선택해주세요:

1️⃣ 체험단 신청
2️⃣ 진행 상황 확인
3️⃣ 사진 제출 (구매/리뷰 캡쳐)
4️⃣ 입금 현황 확인
5️⃣ 기타 문의"""

WELCOME_BACK = """안녕하세요, {name}님! 다시 오셨군요 😊

무엇을 도와드릴까요?

1️⃣ 체험단 신청
2️⃣ 진행 상황 확인
3️⃣ 사진 제출
4️⃣ 입금 현황
5️⃣ 기타 문의"""

# ──────────── STEP 1: 캠페인 선택 ────────────

CAMPAIGN_LIST_HEADER = "현재 모집 중인 체험단입니다:\n"

CAMPAIGN_ITEM = """
{idx}. {product_name}
   🏪 {store_name}
   📦 {option}
   ✅ 남은 수량: {remaining}명
   💰 리뷰비: {review_fee}원"""

CAMPAIGN_ITEM_WITH_IDS = """
{idx}. {product_name}
   🏪 {store_name}
   📦 {option}
   ✅ 남은 수량: {remaining}명
   💰 리뷰비: {review_fee}원
   🆔 내 진행 아이디: {my_ids}"""

CAMPAIGN_LIST_FOOTER = "\n\n원하시는 체험단 번호를 입력해주세요."

NO_CAMPAIGNS = "현재 모집 중인 체험단이 없습니다. 곧 새로운 캠페인이 등록되면 안내드릴게요!"

# ──────────── STEP 2: 계정 수 ────────────

ASK_ACCOUNT_COUNT = """✨ {product_name} ✨
🏪 {store_name}

몇 개 계정으로 진행하시겠습니까?
(숫자를 입력해주세요. 예: 1)"""

# ──────────── STEP 3: 아이디 수집 ────────────

ASK_STORE_IDS = """스토어 아이디를 입력해주세요.
(여러 개면 콤마로 구분. 예: abc123, def456)"""

DUPLICATE_FOUND = """⚠️ 이미 동일한 캠페인에 '{store_id}' 아이디로 신청한 내역이 있습니다.
다른 아이디를 입력해주세요."""

ID_CONFIRMED = """✅ {store_id} 확인"""

# ──────────── STEP 4: 구매 가이드 ────────────

PURCHASE_GUIDE = """📋 구매 가이드를 안내드립니다.

✨ {product_name} ✨
🏪 {store_name}

🔗 상품링크: {product_link}
🔍 키워드: {keyword}
📱 유입방식: {entry_method}
📦 옵션: {option}
💰 결제금액: {payment_amount}원
📝 리뷰가이드: {review_guide}

📌 구매 방법:
1. 위 링크로 접속 또는 키워드 검색
2. 상품 구매 (옵션 확인!)
3. 구매 완료 후 아래 양식을 제출해주세요

⚠️ 유의사항:
- 반드시 위 키워드로 검색 후 구매
- 옵션을 정확히 선택
- 구매 후 양식 제출 필수

✏️ 구매 완료 후 아래 양식을 입력해주세요:

{form_template}"""

# ──────────── STEP 5: 양식 접수 ────────────

FORM_MISSING_FIELDS = """아래 항목이 누락되었습니다:
{missing_list}

다시 전체 양식을 입력해주세요:

{form_template}"""

FORM_RECEIVED = """양식이 접수되었습니다! ✅

📦 {product_name}
🆔 {id_list}
👤 {recipient_name}

📸 이제 구매 캡쳐를 제출해주세요.
🔗 사진 제출: {upload_url}

하단 메뉴 ☰ → 사진제출에서도 가능합니다."""

# ──────────── STEP 6: 구매캡쳐 ────────────

PURCHASE_CAPTURE_REMIND = """구매 캡쳐를 아직 제출하지 않으셨어요!

📸 사진 제출 링크: {upload_url}
또는 ☰ 메뉴 → 사진제출"""

# ──────────── STEP 7: 리뷰캡쳐 ────────────

REVIEW_CAPTURE_REMIND = """리뷰 캡쳐를 아직 제출하지 않으셨어요!

📸 사진 제출 링크: {upload_url}
또는 ☰ 메뉴 → 사진제출

⏰ 리뷰 기한: {deadline}"""

# ──────────── STEP 8: 완료 ────────────

ALL_DONE = """모든 제출이 완료되었습니다! 🎉

확인 후 리뷰비를 입금해드리겠습니다.
☰ 메뉴 → 입금현황에서 확인하실 수 있습니다."""

SETTLEMENT_DONE = """💰 입금 완료!

{product_name} 리뷰비 {amount}원이 입금되었습니다.
이용해주셔서 감사합니다! 😊"""

# ──────────── 공통 ────────────

UNKNOWN_INPUT = "죄송합니다, 이해하지 못했어요. 다시 입력해주시거나 번호를 선택해주세요."

TIMEOUT_WARNING = "⏰ 15분 동안 응답이 없으면 대화가 초기화됩니다."

SESSION_EXPIRED = "⏰ 15분이 지나 대화가 초기화되었습니다. 메뉴에서 다시 선택해주세요."

ERROR_OCCURRED = "죄송합니다, 오류가 발생했습니다. 잠시 후 다시 시도해주세요."

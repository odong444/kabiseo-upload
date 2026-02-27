"""
db_manager.py - PostgreSQL CRUD 매니저

Google Sheets를 대체하는 메인 데이터 저장소.
테이블: campaigns, reviewers, progress
"""

import logging
from datetime import datetime, timedelta
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

from modules.utils import today_str, now_kst, KST

logger = logging.getLogger(__name__)

# 상태 상수
STATUS_APPLIED = "신청"
STATUS_GUIDE_SENT = "가이드전달"
STATUS_PURCHASE_WAIT = "구매캡쳐대기"
STATUS_REVIEW_WAIT = "리뷰대기"
STATUS_REVIEW_DONE = "리뷰제출"
STATUS_PAYMENT_WAIT = "입금대기"
STATUS_SETTLED = "입금완료"
STATUS_TIMEOUT = "타임아웃취소"
STATUS_CANCELLED = "취소"

# 하위 호환
STATUS_PURCHASE_DONE = STATUS_PURCHASE_WAIT
STATUS_FORM_RECEIVED = STATUS_PURCHASE_WAIT

# 완료 상태 (구매캡쳐대기 이상 = 모집수량 차감)
_DONE_STATUSES = (STATUS_PURCHASE_WAIT, STATUS_REVIEW_WAIT, STATUS_REVIEW_DONE,
                  STATUS_PAYMENT_WAIT, STATUS_SETTLED)

# 중복 체크 무시 상태
_DUP_IGNORE_STATUSES = (STATUS_APPLIED, STATUS_GUIDE_SENT,
                        STATUS_TIMEOUT, STATUS_CANCELLED, "")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS campaigns (
    id              TEXT PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    status          TEXT NOT NULL DEFAULT '모집중',
    company         TEXT NOT NULL DEFAULT '',
    campaign_name   TEXT DEFAULT '',
    product_name    TEXT NOT NULL DEFAULT '',
    product_link    TEXT DEFAULT '',
    product_codes   JSONB DEFAULT '{}',
    product_image   TEXT DEFAULT '',
    product_price   INTEGER DEFAULT 0,
    payment_amount  INTEGER DEFAULT 0,
    campaign_type   TEXT DEFAULT '실배송',
    platform        TEXT DEFAULT '',
    options         TEXT DEFAULT '',
    option_list     JSONB DEFAULT '[]',
    keyword         TEXT DEFAULT '',
    keyword_position TEXT DEFAULT '',
    current_rank    TEXT DEFAULT '',
    entry_method    TEXT DEFAULT '',
    total_qty       INTEGER NOT NULL DEFAULT 0,
    daily_qty       INTEGER DEFAULT 0,
    done_qty        INTEGER DEFAULT 0,
    max_daily       INTEGER DEFAULT 0,
    duration_days   INTEGER DEFAULT 0,
    same_day_ship   TEXT DEFAULT '',
    ship_deadline   TEXT DEFAULT '',
    courier         TEXT DEFAULT '',
    use_3pl         BOOLEAN DEFAULT FALSE,
    cost_3pl        INTEGER DEFAULT 0,
    weekend_work    BOOLEAN DEFAULT FALSE,
    review_provided BOOLEAN DEFAULT TRUE,
    review_deadline_days INTEGER DEFAULT 7,
    review_fee      INTEGER DEFAULT 0,
    review_type     TEXT DEFAULT '',
    review_guide    TEXT DEFAULT '',
    review_image_folder TEXT DEFAULT '',
    campaign_guide  TEXT DEFAULT '',
    extra_info      TEXT DEFAULT '',
    allow_duplicate BOOLEAN DEFAULT FALSE,
    monthly_dup_ok  BOOLEAN DEFAULT FALSE,
    buy_time        TEXT DEFAULT '',
    payment_method  TEXT DEFAULT '',
    dwell_time      TEXT DEFAULT '',
    bookmark_required BOOLEAN DEFAULT FALSE,
    alert_required  BOOLEAN DEFAULT FALSE,
    no_ad_click     BOOLEAN DEFAULT FALSE,
    no_blind_account BOOLEAN DEFAULT FALSE,
    reorder_check   BOOLEAN DEFAULT FALSE,
    ship_memo_required BOOLEAN DEFAULT FALSE,
    ship_memo_content  TEXT DEFAULT '',
    ship_memo_link  TEXT DEFAULT '',
    daily_schedule  JSONB DEFAULT '[]',
    start_date      DATE,
    deadline_date   DATE,
    is_public       BOOLEAN DEFAULT TRUE,
    is_selected     BOOLEAN DEFAULT FALSE,
    reward          TEXT DEFAULT '',
    memo            TEXT DEFAULT '',
    promotion_message TEXT DEFAULT '',
    promo_enabled     BOOLEAN DEFAULT FALSE,
    promo_categories  TEXT DEFAULT '',
    promo_start       TEXT DEFAULT '09:00',
    promo_end         TEXT DEFAULT '22:00',
    promo_cooldown    INTEGER DEFAULT 60,
    ai_instructions   TEXT DEFAULT '',
    ai_purchase_instructions TEXT DEFAULT '',
    ai_review_instructions TEXT DEFAULT '',
    max_per_person_daily INTEGER DEFAULT 0,
    exclusive_group TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS reviewers (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    phone           TEXT NOT NULL,
    store_ids       TEXT DEFAULT '',
    kakao_friend    BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    participation   INTEGER DEFAULT 0,
    memo            TEXT DEFAULT '',
    UNIQUE(name, phone)
);

CREATE TABLE IF NOT EXISTS progress (
    id              SERIAL PRIMARY KEY,
    campaign_id     TEXT REFERENCES campaigns(id) ON DELETE SET NULL,
    reviewer_id     INTEGER NOT NULL REFERENCES reviewers(id),
    store_id        TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT '신청',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    recipient_name  TEXT DEFAULT '',
    phone           TEXT DEFAULT '',
    bank            TEXT DEFAULT '',
    account         TEXT DEFAULT '',
    depositor       TEXT DEFAULT '',
    address         TEXT DEFAULT '',
    nickname        TEXT DEFAULT '',
    payment_amount  INTEGER DEFAULT 0,
    order_number    TEXT DEFAULT '',
    purchase_date   DATE,
    purchase_capture_url TEXT DEFAULT '',
    review_deadline DATE,
    review_submit_date DATE,
    review_capture_url TEXT DEFAULT '',
    review_fee      INTEGER DEFAULT 0,
    payment_total   INTEGER DEFAULT 0,
    settlement_date DATE,
    settled_date    DATE,
    is_collected    BOOLEAN DEFAULT FALSE,
    remark          TEXT DEFAULT '',
    last_reminder_date DATE,
    ai_purchase_result  TEXT DEFAULT '',
    ai_purchase_reason  TEXT DEFAULT '',
    ai_review_result    TEXT DEFAULT '',
    ai_review_reason    TEXT DEFAULT '',
    ai_verified_at      TIMESTAMPTZ,
    ai_override         TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);
CREATE INDEX IF NOT EXISTS idx_campaigns_company ON campaigns(company);
CREATE INDEX IF NOT EXISTS idx_reviewers_phone ON reviewers(phone);
CREATE INDEX IF NOT EXISTS idx_reviewers_name_phone ON reviewers(name, phone);
CREATE INDEX IF NOT EXISTS idx_progress_campaign ON progress(campaign_id);
CREATE INDEX IF NOT EXISTS idx_progress_reviewer ON progress(reviewer_id);
CREATE INDEX IF NOT EXISTS idx_progress_status ON progress(status);
CREATE INDEX IF NOT EXISTS idx_progress_created ON progress(created_at);
CREATE INDEX IF NOT EXISTS idx_progress_store ON progress(campaign_id, store_id);

CREATE TABLE IF NOT EXISTS inquiries (
    id              SERIAL PRIMARY KEY,
    reviewer_id     INTEGER REFERENCES reviewers(id),
    reviewer_name   TEXT DEFAULT '',
    reviewer_phone  TEXT DEFAULT '',
    message         TEXT NOT NULL,
    context         TEXT DEFAULT '',
    status          TEXT DEFAULT '대기',
    admin_reply     TEXT DEFAULT '',
    is_urgent       BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    replied_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_inquiries_status ON inquiries(status);
CREATE INDEX IF NOT EXISTS idx_inquiries_created ON inquiries(created_at);

CREATE TABLE IF NOT EXISTS chat_messages (
    id              SERIAL PRIMARY KEY,
    reviewer_id     TEXT NOT NULL,
    sender          TEXT NOT NULL DEFAULT 'user',
    message         TEXT NOT NULL DEFAULT '',
    rating          TEXT DEFAULT '',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_reviewer ON chat_messages(reviewer_id);
CREATE INDEX IF NOT EXISTS idx_chat_created ON chat_messages(created_at);

CREATE TABLE IF NOT EXISTS managers (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    phone           TEXT NOT NULL,
    role            TEXT DEFAULT '담당자',
    receive_kakao   BOOLEAN DEFAULT TRUE,
    notify_start    TEXT DEFAULT '09:00',
    notify_end      TEXT DEFAULT '22:00',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(name, phone)
);

CREATE TABLE IF NOT EXISTS suppliers (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT '',
    biz_number      TEXT DEFAULT '',
    company_name    TEXT DEFAULT '',
    ceo_name        TEXT DEFAULT '',
    address         TEXT DEFAULT '',
    biz_type        TEXT DEFAULT '',
    biz_category    TEXT DEFAULT '',
    bank_account    TEXT DEFAULT '',
    manager_name    TEXT DEFAULT '',
    manager_phone   TEXT DEFAULT '',
    is_default      BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS quotes (
    id              SERIAL PRIMARY KEY,
    status          TEXT DEFAULT '작성중',
    raw_text        TEXT NOT NULL DEFAULT '',
    parsed_data     JSONB DEFAULT '{}',
    supplier_id     INTEGER,
    recipient       TEXT DEFAULT '',
    items           JSONB DEFAULT '[]',
    notes           TEXT DEFAULT '',
    campaign_id     TEXT,
    memo            TEXT DEFAULT '',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_quotes_status ON quotes(status);
CREATE INDEX IF NOT EXISTS idx_quotes_created ON quotes(created_at);

CREATE TABLE IF NOT EXISTS site_settings (
    key             TEXT PRIMARY KEY,
    value           TEXT DEFAULT '',
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS campaign_photos (
    id              SERIAL PRIMARY KEY,
    campaign_id     TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    set_number      INTEGER NOT NULL,
    file_index      INTEGER NOT NULL DEFAULT 0,
    drive_url       TEXT NOT NULL,
    filename        TEXT DEFAULT '',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_campaign_photos_campaign ON campaign_photos(campaign_id);
CREATE INDEX IF NOT EXISTS idx_campaign_photos_set ON campaign_photos(campaign_id, set_number);
"""


class DBManager:
    """PostgreSQL CRUD 매니저 (SheetsManager 대체)"""

    def __init__(self, database_url: str, min_conn: int = 1, max_conn: int = 10):
        self.database_url = database_url
        self.pool = ThreadedConnectionPool(min_conn, max_conn, database_url)
        self._init_schema()
        logger.info("DBManager 초기화 완료")

    def _init_schema(self):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA_SQL)
                # 마이그레이션: 기존 테이블에 새 컬럼 추가
                try:
                    cur.execute("ALTER TABLE progress ADD COLUMN IF NOT EXISTS last_reminder_date DATE")
                except Exception:
                    pass
                try:
                    cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS payment_amount INTEGER DEFAULT 0")
                except Exception:
                    pass
                try:
                    cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS daily_schedule JSONB DEFAULT '[]'")
                    cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS start_date DATE")
                except Exception:
                    pass
                try:
                    cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS product_codes JSONB DEFAULT '{}'")
                except Exception:
                    pass
                try:
                    cur.execute("ALTER TABLE managers ADD COLUMN IF NOT EXISTS notify_start TEXT DEFAULT '09:00'")
                    cur.execute("ALTER TABLE managers ADD COLUMN IF NOT EXISTS notify_end TEXT DEFAULT '22:00'")
                except Exception:
                    pass
                try:
                    cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS promotion_message TEXT DEFAULT ''")
                except Exception:
                    pass
                try:
                    cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS campaign_name TEXT DEFAULT ''")
                except Exception:
                    pass
                try:
                    cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS promo_enabled BOOLEAN DEFAULT FALSE")
                    cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS promo_categories TEXT DEFAULT ''")
                    cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS promo_start TEXT DEFAULT '09:00'")
                    cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS promo_end TEXT DEFAULT '22:00'")
                    cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS promo_cooldown INTEGER DEFAULT 60")
                except Exception:
                    pass
                # AI 검수 컬럼
                try:
                    cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS ai_instructions TEXT DEFAULT ''")
                    cur.execute("ALTER TABLE progress ADD COLUMN IF NOT EXISTS ai_purchase_result TEXT DEFAULT ''")
                    cur.execute("ALTER TABLE progress ADD COLUMN IF NOT EXISTS ai_purchase_reason TEXT DEFAULT ''")
                    cur.execute("ALTER TABLE progress ADD COLUMN IF NOT EXISTS ai_review_result TEXT DEFAULT ''")
                    cur.execute("ALTER TABLE progress ADD COLUMN IF NOT EXISTS ai_review_reason TEXT DEFAULT ''")
                    cur.execute("ALTER TABLE progress ADD COLUMN IF NOT EXISTS ai_verified_at TIMESTAMPTZ")
                    cur.execute("ALTER TABLE progress ADD COLUMN IF NOT EXISTS ai_override TEXT DEFAULT ''")
                except Exception:
                    pass
                # 1인 일일 제한
                try:
                    cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS max_per_person_daily INTEGER DEFAULT 0")
                except Exception:
                    pass
                # AI 구매/리뷰 검수 지침 분리
                try:
                    cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS ai_purchase_instructions TEXT DEFAULT ''")
                    cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS ai_review_instructions TEXT DEFAULT ''")
                except Exception:
                    pass
                # 사진 세트 할당 번호
                try:
                    cur.execute("ALTER TABLE progress ADD COLUMN IF NOT EXISTS photo_set_number INTEGER")
                except Exception:
                    pass
                # 동시진행그룹
                try:
                    cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS exclusive_group TEXT DEFAULT ''")
                except Exception:
                    pass
                # 마이그레이션: progress.campaign_id FK를 ON DELETE SET NULL로 변경 + NOT NULL 해제
                try:
                    cur.execute("""
                        DO $$
                        BEGIN
                            -- NOT NULL 제약 해제
                            ALTER TABLE progress ALTER COLUMN campaign_id DROP NOT NULL;
                            -- 기존 FK 제약조건 찾아서 삭제 후 재생성
                            IF EXISTS (
                                SELECT 1 FROM information_schema.table_constraints
                                WHERE table_name = 'progress' AND constraint_type = 'FOREIGN KEY'
                                AND constraint_name IN (
                                    SELECT constraint_name FROM information_schema.constraint_column_usage
                                    WHERE table_name = 'campaigns' AND column_name = 'id'
                                )
                            ) THEN
                                EXECUTE (
                                    SELECT 'ALTER TABLE progress DROP CONSTRAINT ' || constraint_name
                                    FROM information_schema.table_constraints tc
                                    JOIN information_schema.constraint_column_usage ccu USING (constraint_name, constraint_schema)
                                    WHERE tc.table_name = 'progress' AND tc.constraint_type = 'FOREIGN KEY'
                                    AND ccu.table_name = 'campaigns' AND ccu.column_name = 'id'
                                    LIMIT 1
                                );
                                ALTER TABLE progress ADD CONSTRAINT progress_campaign_id_fkey
                                    FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE SET NULL;
                            END IF;
                        END $$;
                    """)
                except Exception:
                    pass
            conn.commit()
        logger.info("DB 스키마 확인/생성 완료")

    @contextmanager
    def _conn(self):
        conn = self.pool.getconn()
        try:
            yield conn
        finally:
            self.pool.putconn(conn)

    def _fetchall(self, sql, params=None):
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]

    def _fetchone(self, sql, params=None):
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                return dict(row) if row else None

    def _execute(self, sql, params=None):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()

    def _execute_returning(self, sql, params=None):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                result = cur.fetchone()
            conn.commit()
            return result[0] if result else None

    # ─────────── reviewers ───────────

    def upsert_reviewer(self, name: str, phone: str) -> int:
        """로그인 시 리뷰어 upsert. 없으면 추가, 있으면 id 반환."""
        sql = """
            INSERT INTO reviewers (name, phone, created_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (name, phone) DO UPDATE SET updated_at = NOW()
            RETURNING id
        """
        return self._execute_returning(sql, (name, phone))

    def get_reviewer(self, name: str, phone: str) -> dict | None:
        return self._fetchone(
            "SELECT * FROM reviewers WHERE name = %s AND phone = %s",
            (name, phone)
        )

    def get_reviewer_by_id(self, reviewer_id: int) -> dict | None:
        return self._fetchone("SELECT * FROM reviewers WHERE id = %s", (reviewer_id,))

    def update_kakao_friend(self, name: str, phone: str, status: bool):
        """카카오 친구추가 상태 업데이트"""
        self._execute(
            "UPDATE reviewers SET kakao_friend = %s, updated_at = NOW() WHERE name = %s AND phone = %s",
            (status, name, phone)
        )

    def update_reviewer_store_ids(self, name: str, phone: str, store_id: str):
        """캠페인 등록 시 아이디목록 + 참여횟수 업데이트"""
        reviewer = self.get_reviewer(name, phone)
        if not reviewer:
            return
        existing = reviewer.get("store_ids", "") or ""
        id_list = [x.strip() for x in existing.split(",") if x.strip()]
        if store_id not in id_list:
            id_list.append(store_id)
        self._execute(
            """UPDATE reviewers
               SET store_ids = %s, participation = participation + 1, updated_at = NOW()
               WHERE name = %s AND phone = %s""",
            (", ".join(id_list), name, phone)
        )

    def get_all_reviewers_db(self) -> list[dict]:
        return self._fetchall("SELECT * FROM reviewers ORDER BY created_at DESC")

    # ─────────── campaigns ───────────

    def get_all_campaigns(self) -> list[dict]:
        rows = self._fetchall("SELECT * FROM campaigns ORDER BY created_at DESC")
        # 하위 호환: 시트 컬럼명 매핑
        return [self._campaign_to_sheet_dict(r) for r in rows]

    def get_campaign_stats(self) -> dict:
        """캠페인별 진행 통계를 SQL 집계로 반환. {campaign_id: {...stats}}"""
        sql = """
            SELECT campaign_id,
                   COUNT(*) FILTER (WHERE status NOT IN ('취소','타임아웃취소')) AS active_count,
                   COUNT(*) FILTER (WHERE status = '입금완료') AS done_count,
                   COUNT(*) FILTER (WHERE status IN ('리뷰대기','리뷰제출','입금대기','입금완료')) AS purchase_done,
                   COUNT(*) FILTER (WHERE status IN ('리뷰제출','입금대기','입금완료')) AS review_done,
                   COUNT(*) FILTER (WHERE status = '입금대기') AS settlement_pending,
                   COUNT(*) FILTER (WHERE status = '입금완료') AS settlement_done,
                   COUNT(*) FILTER (WHERE created_at::date = CURRENT_DATE
                                    AND status NOT IN ('취소','타임아웃취소')) AS today_count
            FROM progress
            GROUP BY campaign_id
        """
        rows = self._fetchall(sql)
        result = {}
        for r in rows:
            result[r["campaign_id"]] = {
                "active": r["active_count"],
                "done": r["done_count"],
                "purchase_done": r["purchase_done"],
                "review_done": r["review_done"],
                "settlement_pending": r["settlement_pending"],
                "settlement_done": r["settlement_done"],
                "today": r["today_count"],
            }
        return result

    def get_campaign_by_id(self, campaign_id: str) -> dict | None:
        row = self._fetchone("SELECT * FROM campaigns WHERE id = %s", (campaign_id,))
        return self._campaign_to_sheet_dict(row) if row else None

    def create_campaign(self, data: dict) -> str:
        """캠페인 생성. data는 시트 컬럼명 형태도 허용."""
        d = self._campaign_from_sheet_dict(data)
        sql = """
            INSERT INTO campaigns (
                id, status, company, campaign_name, product_name, product_link, product_codes, product_image,
                product_price, payment_amount, campaign_type, platform, options, option_list,
                keyword, keyword_position, current_rank, entry_method,
                total_qty, daily_qty, done_qty, max_daily, duration_days,
                same_day_ship, ship_deadline, courier, use_3pl, cost_3pl,
                weekend_work, review_provided, review_deadline_days, review_fee,
                review_type, review_guide, review_image_folder, campaign_guide,
                extra_info, allow_duplicate, monthly_dup_ok, buy_time,
                payment_method, dwell_time, bookmark_required, alert_required,
                no_ad_click, no_blind_account, reorder_check,
                ship_memo_required, ship_memo_content, ship_memo_link,
                daily_schedule, start_date,
                deadline_date, is_public, is_selected, reward, memo,
                promotion_message,
                promo_enabled, promo_categories, promo_start, promo_end, promo_cooldown,
                exclusive_group
            ) VALUES (
                %(id)s, %(status)s, %(company)s, %(campaign_name)s, %(product_name)s, %(product_link)s,
                %(product_codes)s, %(product_image)s, %(product_price)s, %(payment_amount)s, %(campaign_type)s, %(platform)s,
                %(options)s, %(option_list)s, %(keyword)s, %(keyword_position)s,
                %(current_rank)s, %(entry_method)s, %(total_qty)s, %(daily_qty)s,
                %(done_qty)s, %(max_daily)s, %(duration_days)s, %(same_day_ship)s,
                %(ship_deadline)s, %(courier)s, %(use_3pl)s, %(cost_3pl)s,
                %(weekend_work)s, %(review_provided)s, %(review_deadline_days)s,
                %(review_fee)s, %(review_type)s, %(review_guide)s,
                %(review_image_folder)s, %(campaign_guide)s, %(extra_info)s,
                %(allow_duplicate)s, %(monthly_dup_ok)s, %(buy_time)s,
                %(payment_method)s, %(dwell_time)s, %(bookmark_required)s,
                %(alert_required)s, %(no_ad_click)s, %(no_blind_account)s,
                %(reorder_check)s, %(ship_memo_required)s, %(ship_memo_content)s,
                %(ship_memo_link)s, %(daily_schedule)s, %(start_date)s,
                %(deadline_date)s, %(is_public)s,
                %(is_selected)s, %(reward)s, %(memo)s,
                %(promotion_message)s,
                %(promo_enabled)s, %(promo_categories)s, %(promo_start)s, %(promo_end)s, %(promo_cooldown)s,
                %(exclusive_group)s
            )
        """
        self._execute(sql, d)
        return d["id"]

    def update_campaign(self, campaign_id: str, data: dict):
        """캠페인 필드 업데이트. data는 시트 컬럼명 키."""
        field_map = self._CAMPAIGN_FIELD_MAP
        sets = []
        params = []
        for k, v in data.items():
            db_col = field_map.get(k, k)
            # DB 컬럼에 해당하는 것만
            if db_col in self._CAMPAIGN_COLUMNS:
                sets.append(f"{db_col} = %s")
                params.append(self._convert_campaign_value(db_col, v))
        if not sets:
            return
        sets.append("updated_at = NOW()")
        params.append(campaign_id)
        sql = f"UPDATE campaigns SET {', '.join(sets)} WHERE id = %s"
        self._execute(sql, params)

    # 캠페인 시트↔DB 컬럼 매핑
    _CAMPAIGN_FIELD_MAP = {
        "캠페인ID": "id", "상태": "status", "업체명": "company", "캠페인명": "campaign_name",
        "상품명": "product_name", "상품링크": "product_link", "상품코드": "product_codes",
        "상품이미지": "product_image", "상품금액": "product_price",
        "캠페인유형": "campaign_type", "플랫폼": "platform",
        "옵션": "options", "옵션목록": "option_list",
        "키워드": "keyword", "키워드위치": "keyword_position",
        "현재순위": "current_rank", "유입방식": "entry_method",
        "총수량": "total_qty", "일수량": "daily_qty",
        "완료수량": "done_qty", "일최대건": "max_daily",
        "진행일수": "duration_days",
        "당일발송": "same_day_ship", "발송마감": "ship_deadline",
        "택배사": "courier", "3PL사용": "use_3pl", "3PL비용": "cost_3pl",
        "주말작업": "weekend_work", "리뷰제공": "review_provided",
        "리뷰기한일수": "review_deadline_days", "리뷰비": "review_fee",
        "리뷰타입": "review_type", "리뷰가이드내용": "review_guide",
        "리뷰이미지폴더": "review_image_folder",
        "캠페인가이드": "campaign_guide", "추가안내사항": "extra_info",
        "중복허용": "allow_duplicate", "한달중복허용": "monthly_dup_ok",
        "구매가능시간": "buy_time", "결제방법": "payment_method",
        "체류시간": "dwell_time", "상품찜필수": "bookmark_required",
        "알림받기필수": "alert_required", "광고클릭금지": "no_ad_click",
        "블라인드계정금지": "no_blind_account", "재구매확인": "reorder_check",
        "배송메모필수": "ship_memo_required", "배송메모내용": "ship_memo_content",
        "배송메모안내링크": "ship_memo_link", "신청마감일": "deadline_date",
        "공개여부": "is_public", "선정여부": "is_selected",
        "리워드": "reward", "메모": "memo",
        "등록일": "created_at", "결제금액": "payment_amount",
        "리뷰가이드": "review_guide",
        "일정": "daily_schedule", "시작일": "start_date",
        "홍보메시지": "promotion_message",
        "홍보활성": "promo_enabled",
        "홍보카테고리": "promo_categories",
        "홍보시작시간": "promo_start",
        "홍보종료시간": "promo_end",
        "홍보주기": "promo_cooldown",
        "AI검수지침": "ai_instructions",
        "AI구매검수지침": "ai_purchase_instructions",
        "AI리뷰검수지침": "ai_review_instructions",
        "1인일일제한": "max_per_person_daily",
        "동시진행그룹": "exclusive_group",
    }

    _CAMPAIGN_COLUMNS = {
        "id", "status", "company", "campaign_name", "product_name", "product_link",
        "product_codes", "product_image", "product_price", "payment_amount",
        "campaign_type", "platform",
        "options", "option_list", "keyword", "keyword_position",
        "current_rank", "entry_method", "total_qty", "daily_qty",
        "done_qty", "max_daily", "duration_days", "same_day_ship",
        "ship_deadline", "courier", "use_3pl", "cost_3pl",
        "weekend_work", "review_provided", "review_deadline_days",
        "review_fee", "review_type", "review_guide",
        "review_image_folder", "campaign_guide", "extra_info",
        "allow_duplicate", "monthly_dup_ok", "buy_time",
        "payment_method", "dwell_time", "bookmark_required",
        "alert_required", "no_ad_click", "no_blind_account",
        "reorder_check", "ship_memo_required", "ship_memo_content",
        "ship_memo_link", "daily_schedule", "start_date",
        "deadline_date", "is_public",
        "is_selected", "reward", "memo",
        "promotion_message",
        "promo_enabled", "promo_categories",
        "promo_start", "promo_end", "promo_cooldown",
        "ai_instructions", "ai_purchase_instructions", "ai_review_instructions",
        "max_per_person_daily",
        "exclusive_group",
    }

    _BOOL_COLUMNS = {
        "use_3pl", "weekend_work", "review_provided", "allow_duplicate",
        "monthly_dup_ok", "bookmark_required", "alert_required",
        "no_ad_click", "no_blind_account", "reorder_check",
        "ship_memo_required", "is_public", "is_selected",
        "promo_enabled",
    }

    _INT_COLUMNS = {
        "product_price", "payment_amount", "total_qty", "daily_qty",
        "done_qty", "max_daily", "duration_days", "cost_3pl",
        "review_deadline_days", "review_fee",
        "promo_cooldown",
    }

    def _convert_campaign_value(self, col: str, value):
        if col in self._BOOL_COLUMNS:
            if isinstance(value, bool):
                return value
            return str(value).strip().upper() in ("Y", "O", "예", "TRUE", "1", "허용")
        if col in self._INT_COLUMNS:
            try:
                return int(str(value).replace(",", "").strip() or "0")
            except (ValueError, TypeError):
                return 0
        if col == "option_list":
            import json
            if isinstance(value, str):
                try:
                    return json.dumps(json.loads(value))
                except Exception:
                    return "[]"
            return json.dumps(value) if value else "[]"
        if col == "product_codes":
            import json
            if isinstance(value, dict):
                return json.dumps(value)
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    return "{}"
                try:
                    return json.dumps(json.loads(value))
                except Exception:
                    return "{}"
            return "{}"
        if col in ("deadline_date", "start_date"):
            if not value or not str(value).strip():
                return None
            return str(value).strip()
        if col == "daily_schedule":
            import json
            if isinstance(value, list):
                return json.dumps(value)
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    return "[]"
                try:
                    return json.dumps(json.loads(value))
                except Exception:
                    # "3,5,4,3,4,2" 형태 파싱
                    try:
                        nums = [int(x.strip()) for x in value.split(",") if x.strip()]
                        return json.dumps(nums)
                    except Exception:
                        return "[]"
            return "[]"
        return str(value) if value is not None else ""

    def _campaign_from_sheet_dict(self, data: dict) -> dict:
        """시트 컬럼명 dict → DB 컬럼명 dict (INSERT용)"""
        import uuid
        result = {col: None for col in self._CAMPAIGN_COLUMNS}
        result["id"] = data.get("캠페인ID", data.get("id", str(uuid.uuid4())[:8]))
        result["status"] = data.get("상태", data.get("status", "모집중"))

        for sheet_key, db_col in self._CAMPAIGN_FIELD_MAP.items():
            if sheet_key in data and db_col in self._CAMPAIGN_COLUMNS:
                result[db_col] = self._convert_campaign_value(db_col, data[sheet_key])

        # DB 컬럼명 직접 전달도 허용
        for db_col in self._CAMPAIGN_COLUMNS:
            if db_col in data and result.get(db_col) is None:
                result[db_col] = self._convert_campaign_value(db_col, data[db_col])

        # 기본값
        for col in self._BOOL_COLUMNS:
            if result.get(col) is None:
                result[col] = col in ("review_provided", "is_public")
        for col in self._INT_COLUMNS:
            if result.get(col) is None:
                result[col] = 0
        for col in self._CAMPAIGN_COLUMNS:
            if result.get(col) is None:
                if col in ("deadline_date", "start_date"):
                    continue  # DATE 컬럼은 빈 문자열 대신 NULL 유지
                if col == "daily_schedule":
                    result[col] = "[]"
                    continue
                if col == "product_codes":
                    result[col] = "{}"
                    continue
                if col == "option_list":
                    result[col] = "[]"
                    continue
                result[col] = ""

        return result

    def _campaign_to_sheet_dict(self, row: dict) -> dict:
        """DB row → 시트 컬럼명 dict (하위 호환)"""
        if not row:
            return {}
        reverse_map = {v: k for k, v in self._CAMPAIGN_FIELD_MAP.items()}
        result = {"id": row["id"]}
        for db_col, value in row.items():
            sheet_key = reverse_map.get(db_col, db_col)
            if db_col in self._BOOL_COLUMNS:
                result[sheet_key] = "Y" if value else "N"
            elif db_col in self._INT_COLUMNS:
                result[sheet_key] = str(value) if value else "0"
            elif db_col == "created_at":
                result["등록일"] = value.strftime("%Y-%m-%d") if value else ""
            elif db_col == "deadline_date":
                result["신청마감일"] = str(value) if value else ""
            elif db_col == "start_date":
                result["시작일"] = str(value) if value else ""
            elif db_col == "product_codes":
                result["상품코드"] = value if isinstance(value, dict) else {}
            elif db_col == "daily_schedule":
                import json
                if isinstance(value, list):
                    result["일정"] = value
                elif isinstance(value, str):
                    try:
                        result["일정"] = json.loads(value)
                    except Exception:
                        result["일정"] = []
                else:
                    result["일정"] = value if isinstance(value, list) else []
            elif db_col == "option_list":
                import json
                result["옵션목록"] = json.dumps(value) if value else "[]"
            else:
                result[sheet_key] = str(value) if value is not None else ""
        # _row_idx 호환 (PK id를 사용)
        result["_row_idx"] = row["id"]
        result["캠페인ID"] = row["id"]
        return result

    # ─────────── progress (카비서_정리) ───────────

    def add_progress(self, data: dict) -> int:
        """진행건 추가 (캠페인 신청). data는 시트 컬럼명 형태."""
        campaign_id = data.get("캠페인ID", "")
        name = data.get("진행자이름", "")
        phone = data.get("진행자연락처", "")

        # reviewer 확보
        reviewer = self.get_reviewer(name, phone)
        if not reviewer:
            reviewer_id = self.upsert_reviewer(name, phone)
        else:
            reviewer_id = reviewer["id"]

        sql = """
            INSERT INTO progress (
                campaign_id, reviewer_id, store_id, status, created_at,
                recipient_name, phone, bank, account, depositor,
                address, nickname, payment_amount, review_fee, remark
            ) VALUES (
                %s, %s, %s, %s, NOW(),
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            ) RETURNING id
        """
        progress_id = self._execute_returning(sql, (
            campaign_id,
            reviewer_id,
            data.get("아이디", ""),
            data.get("상태", STATUS_APPLIED),
            data.get("수취인명", ""),
            data.get("연락처", ""),
            data.get("은행", ""),
            data.get("계좌", ""),
            data.get("예금주", ""),
            data.get("주소", ""),
            data.get("닉네임", ""),
            self._safe_int(data.get("결제금액", 0)),
            self._safe_int(data.get("리뷰비", 0)),
            data.get("비고", ""),
        ))
        return progress_id

    def _safe_int(self, v) -> int:
        try:
            return int(str(v).replace(",", "").strip() or "0")
        except (ValueError, TypeError):
            return 0

    def _progress_to_sheet_dict(self, row: dict) -> dict:
        """progress DB row → 시트 컬럼명 dict (하위 호환)"""
        if not row:
            return {}
        # reviewer 정보 조회
        reviewer = self.get_reviewer_by_id(row["reviewer_id"]) if row.get("reviewer_id") else {}
        campaign = self.get_campaign_by_id(row["campaign_id"]) if row.get("campaign_id") else {}

        result = {
            "_row_idx": row["id"],
            "id": row["id"],
            "캠페인ID": row.get("campaign_id", ""),
            "업체명": campaign.get("업체명", "") if campaign else "",
            "날짜": row["created_at"].astimezone(KST).strftime("%Y-%m-%d %H:%M") if row.get("created_at") else "",
            "created_at_iso": row["created_at"].astimezone(KST).isoformat() if row.get("created_at") else "",
            "제품명": (campaign.get("캠페인명", "") or campaign.get("상품명", "")) if campaign else "",
            "수취인명": row.get("recipient_name", ""),
            "연락처": row.get("phone", ""),
            "은행": row.get("bank", ""),
            "계좌": row.get("account", ""),
            "예금주": row.get("depositor", ""),
            "결제금액": str(row.get("payment_amount", 0) or ""),
            "아이디": row.get("store_id", ""),
            "주문번호": row.get("order_number", ""),
            "주소": row.get("address", ""),
            "닉네임": row.get("nickname", ""),
            "진행자이름": reviewer.get("name", "") if reviewer else "",
            "진행자연락처": reviewer.get("phone", "") if reviewer else "",
            "카카오친구": reviewer.get("kakao_friend", False) if reviewer else False,
            "상태": row.get("status", ""),
            "구매일": str(row["purchase_date"]) if row.get("purchase_date") else "",
            "구매캡쳐링크": row.get("purchase_capture_url", ""),
            "리뷰기한": str(row["review_deadline"]) if row.get("review_deadline") else "",
            "리뷰제출일": str(row["review_submit_date"]) if row.get("review_submit_date") else "",
            "리뷰캡쳐링크": row.get("review_capture_url", ""),
            "리뷰비": str(row.get("review_fee", 0) or ""),
            "입금금액": str(row.get("payment_total", 0) or ""),
            "입금정리": str(row["settlement_date"]) if row.get("settlement_date") else "",
            "입금완료": str(row["settled_date"]) if row.get("settled_date") else "",
            "회수여부": "Y" if row.get("is_collected") else "",
            "비고": row.get("remark", ""),
            "AI구매검수": row.get("ai_purchase_result", ""),
            "AI구매사유": row.get("ai_purchase_reason", ""),
            "AI리뷰검수": row.get("ai_review_result", ""),
            "AI리뷰사유": row.get("ai_review_reason", ""),
            "AI검수시간": row["ai_verified_at"].astimezone(KST).strftime("%Y-%m-%d %H:%M") if row.get("ai_verified_at") else "",
            "AI관리자판정": row.get("ai_override", ""),
            "사진세트": row.get("photo_set_number"),
        }
        return result

    def search_by_name_phone(self, name: str, phone: str) -> list[dict]:
        """진행자 이름+연락처로 전체 건 검색"""
        reviewer = self.get_reviewer(name, phone)
        if not reviewer:
            return []
        rows = self._fetchall(
            "SELECT * FROM progress WHERE reviewer_id = %s ORDER BY created_at DESC",
            (reviewer["id"],)
        )
        return [self._progress_to_sheet_dict(r) for r in rows]

    def search_by_depositor(self, capture_type: str, name: str) -> list[dict]:
        """예금주명으로 검색"""
        target_status = STATUS_PURCHASE_WAIT if capture_type == "purchase" else STATUS_REVIEW_WAIT
        rows = self._fetchall(
            "SELECT * FROM progress WHERE depositor = %s AND status = %s",
            (name, target_status)
        )
        return [self._progress_to_sheet_dict(r) for r in rows]

    def search_by_name_phone_or_depositor(self, capture_type: str, query: str,
                                           phone: str = "") -> list[dict]:
        """진행자/수취인/예금주로 검색 (사진 제출 대상)"""
        target_statuses = (STATUS_PURCHASE_WAIT,) if capture_type == "purchase" else (STATUS_REVIEW_WAIT,)

        results = []
        # 1. reviewer로 검색
        if phone:
            reviewer = self.get_reviewer(query, phone)
            if reviewer:
                rows = self._fetchall(
                    "SELECT * FROM progress WHERE reviewer_id = %s AND status = ANY(%s)",
                    (reviewer["id"], list(target_statuses))
                )
                results.extend(rows)

        # 2. 예금주로 검색
        rows = self._fetchall(
            "SELECT * FROM progress WHERE depositor = %s AND status = ANY(%s)",
            (query, list(target_statuses))
        )
        # 중복 제거
        existing_ids = {r["id"] for r in results}
        for r in rows:
            if r["id"] not in existing_ids:
                results.append(r)

        return [self._progress_to_sheet_dict(r) for r in results]

    def get_reviewer_items(self, name: str, phone: str) -> dict:
        """리뷰어의 진행현황: 진행중/완료 분류"""
        all_items = self.search_by_name_phone(name, phone)
        in_progress = []
        completed = []
        for item in all_items:
            status = item.get("상태", "")
            if status in (STATUS_SETTLED, STATUS_REVIEW_DONE, STATUS_PAYMENT_WAIT):
                completed.append(item)
            elif status in (STATUS_CANCELLED, STATUS_TIMEOUT):
                continue
            else:
                in_progress.append(item)
        return {"in_progress": in_progress, "completed": completed}

    def get_payment_info(self, name: str, phone: str) -> dict:
        """입금현황 조회"""
        all_items = self.search_by_name_phone(name, phone)
        paid = []
        pending = []
        no_review = []
        for item in all_items:
            status = item.get("상태", "")
            if status == STATUS_SETTLED:
                paid.append(item)
            elif status == STATUS_REVIEW_DONE:
                pending.append(item)
            elif status == STATUS_REVIEW_WAIT:
                no_review.append(item)
        return {"paid": paid, "pending": pending, "no_review": no_review}

    def get_user_prev_info(self, name: str, phone: str) -> dict:
        """유저의 가장 최근 등록 정보에서 은행/계좌/예금주/주소 가져오기"""
        reviewer = self.get_reviewer(name, phone)
        if not reviewer:
            return {}
        row = self._fetchone(
            """SELECT bank, account, depositor, address FROM progress
               WHERE reviewer_id = %s AND bank != '' ORDER BY created_at DESC LIMIT 1""",
            (reviewer["id"],)
        )
        if not row:
            return {}
        result = {}
        for k, v in {"은행": "bank", "계좌": "account", "예금주": "depositor", "주소": "address"}.items():
            val = row.get(v, "")
            if val:
                result[k] = val
        return result

    def get_user_bank_presets(self, name: str, phone: str) -> list[dict]:
        """유저의 과거 계좌 정보 목록 (중복 제거)"""
        reviewer = self.get_reviewer(name, phone)
        if not reviewer:
            return []
        rows = self._fetchall(
            """SELECT DISTINCT ON (bank, account) bank, account, depositor
               FROM progress
               WHERE reviewer_id = %s AND bank != '' AND account != ''
               ORDER BY bank, account, created_at DESC""",
            (reviewer["id"],)
        )
        return [{"은행": r["bank"], "계좌": r["account"], "예금주": r["depositor"] or ""} for r in rows]

    def get_user_campaign_ids(self, name: str, phone: str, campaign_id: str) -> list[str]:
        """특정 캠페인에 해당 유저가 실제 진행 중인 아이디 목록"""
        reviewer = self.get_reviewer(name, phone)
        if not reviewer:
            return []
        rows = self._fetchall(
            """SELECT store_id FROM progress
               WHERE reviewer_id = %s AND campaign_id = %s
               AND status NOT IN %s AND store_id != ''""",
            (reviewer["id"], campaign_id, _DUP_IGNORE_STATUSES)
        )
        return [r["store_id"] for r in rows]

    def update_after_upload(self, capture_type: str, progress_id: int, drive_link: str):
        """업로드 완료 후 상태+링크 업데이트"""
        if capture_type == "purchase":
            self._execute(
                """UPDATE progress SET purchase_capture_url = %s, status = %s, updated_at = NOW()
                   WHERE id = %s""",
                (drive_link, STATUS_REVIEW_WAIT, progress_id)
            )
        elif capture_type == "review":
            # 반려 사유 클리어
            row = self._fetchone("SELECT remark FROM progress WHERE id = %s", (progress_id,))
            remark_update = ""
            if row and row.get("remark", "").startswith("반려"):
                remark_update = ", remark = ''"

            self._execute(
                f"""UPDATE progress SET review_capture_url = %s, status = %s,
                    review_submit_date = CURRENT_DATE, updated_at = NOW(){remark_update}
                    WHERE id = %s""",
                (drive_link, STATUS_REVIEW_DONE, progress_id)
            )

    def update_status(self, progress_id: int, status: str):
        self._execute(
            "UPDATE progress SET status = %s, updated_at = NOW() WHERE id = %s",
            (status, progress_id)
        )

    def update_progress_field(self, progress_id: int, field: str, value):
        """progress 테이블의 단일 필드 업데이트"""
        # 시트 컬럼명 → DB 컬럼명 매핑
        field_map = {
            "수취인명": "recipient_name", "연락처": "phone",
            "은행": "bank", "계좌": "account", "예금주": "depositor",
            "주소": "address", "닉네임": "nickname", "아이디": "store_id",
            "결제금액": "payment_amount", "주문번호": "order_number",
            "구매일": "purchase_date", "리뷰기한": "review_deadline",
            "리뷰비": "review_fee", "입금금액": "payment_total",
            "비고": "remark", "상태": "status",
            "구매캡쳐링크": "purchase_capture_url",
            "리뷰캡쳐링크": "review_capture_url",
            "리뷰제출일": "review_submit_date",
        }
        db_col = field_map.get(field, field)
        self._execute(
            f"UPDATE progress SET {db_col} = %s, updated_at = NOW() WHERE id = %s",
            (value, progress_id)
        )

    def delete_progress(self, progress_id: int) -> bool:
        """progress 행 삭제"""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM progress WHERE id = %s", (progress_id,))
                ok = cur.rowcount > 0
            conn.commit()
        return ok

    def approve_review(self, progress_id: int):
        """검수 승인 → 입금대기"""
        self.update_status(progress_id, STATUS_PAYMENT_WAIT)

    def reject_review(self, progress_id: int, reason: str = ""):
        """검수 반려 → 리뷰대기 + 링크 삭제"""
        remark = f"반려: {reason}" if reason else "반려"
        self._execute(
            """UPDATE progress SET status = %s, review_capture_url = '',
               review_submit_date = NULL, remark = %s, updated_at = NOW()
               WHERE id = %s""",
            (STATUS_REVIEW_WAIT, remark, progress_id)
        )

    def restore_from_timeout(self, progress_id: int):
        """타임아웃취소 → 가이드전달로 복원"""
        self.update_status(progress_id, STATUS_GUIDE_SENT)

    def process_settlement(self, progress_id: int, amount: str):
        """정산 처리"""
        self._execute(
            """UPDATE progress SET status = %s, payment_total = %s,
               settlement_date = CURRENT_DATE, settled_date = CURRENT_DATE,
               updated_at = NOW()
               WHERE id = %s""",
            (STATUS_SETTLED, self._safe_int(amount), progress_id)
        )

    def get_row_dict(self, progress_id: int) -> dict:
        """progress ID로 시트 호환 dict 반환"""
        row = self._fetchone("SELECT * FROM progress WHERE id = %s", (progress_id,))
        return self._progress_to_sheet_dict(row) if row else {}

    def get_all_reviewers(self) -> list[dict]:
        """전체 progress 목록 (시트 호환)"""
        rows = self._fetchall("SELECT * FROM progress ORDER BY created_at DESC")
        return [self._progress_to_sheet_dict(r) for r in rows]

    def check_duplicate(self, campaign_id: str, store_id: str) -> bool:
        """같은 캠페인ID + 같은 아이디 중복 여부"""
        row = self._fetchone(
            """SELECT 1 FROM progress
               WHERE campaign_id = %s AND store_id = %s
               AND status NOT IN %s LIMIT 1""",
            (campaign_id, store_id, _DUP_IGNORE_STATUSES)
        )
        return row is not None

    def check_exclusive_group_duplicate(self, campaign_id: str, store_id: str) -> str | None:
        """동시진행그룹 내 다른 캠페인에서 동일 아이디 사용 중인지 확인.
        Returns: 중복된 캠페인명 or None
        """
        # 현재 캠페인의 exclusive_group 확인
        camp = self._fetchone(
            "SELECT exclusive_group FROM campaigns WHERE id = %s", (campaign_id,)
        )
        if not camp:
            return None
        group = (camp.get("exclusive_group") or "").strip()
        if not group:
            return None

        # 같은 그룹의 다른 캠페인에서 해당 아이디 진행중인지
        row = self._fetchone(
            """SELECT c.campaign_name
               FROM progress p
               JOIN campaigns c ON p.campaign_id = c.id
               WHERE c.exclusive_group = %s
               AND p.campaign_id != %s
               AND p.store_id = %s
               AND p.status NOT IN %s
               LIMIT 1""",
            (group, campaign_id, store_id, _DUP_IGNORE_STATUSES),
        )
        return row["campaign_name"] if row else None

    def cancel_stale_rows(self, hours: int = 1) -> int:
        """N시간 이상 신청/가이드전달 상태 → 타임아웃취소"""
        cutoff = now_kst() - timedelta(hours=hours)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE progress SET status = %s, updated_at = NOW()
                       WHERE status IN (%s, %s) AND created_at < %s""",
                    (STATUS_TIMEOUT, STATUS_APPLIED, STATUS_GUIDE_SENT, cutoff)
                )
                count = cur.rowcount
            conn.commit()
        if count:
            logger.info("DB 기반 타임아웃 취소: %d건 (%d시간 초과)", count, hours)
        return count

    def cancel_by_timeout(self, name: str, phone: str, campaign_id: str, store_ids: list[str]):
        """타임아웃 취소: 해당 유저의 해당 캠페인 신청/가이드전달 → 타임아웃취소"""
        reviewer = self.get_reviewer(name, phone)
        if not reviewer:
            return 0
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE progress SET status = %s, updated_at = NOW()
                       WHERE reviewer_id = %s AND campaign_id = %s
                       AND store_id = ANY(%s) AND status IN (%s, %s)""",
                    (STATUS_TIMEOUT, reviewer["id"], campaign_id,
                     store_ids, STATUS_APPLIED, STATUS_GUIDE_SENT)
                )
                count = cur.rowcount
            conn.commit()
        return count

    def check_repurchase(self, name: str, phone: str, campaign_id: str) -> list[dict]:
        """같은 상품코드를 가진 다른 캠페인에서 리뷰어의 이전 구매 이력 조회.
        Returns: [{"campaign_id": ..., "product_name": ..., "status": ...}]
        """
        # 현재 캠페인의 product_codes 조회
        campaign = self._fetchone(
            "SELECT product_codes FROM campaigns WHERE id = %s", (campaign_id,)
        )
        if not campaign or not campaign.get("product_codes"):
            return []

        codes = campaign["product_codes"]
        if isinstance(codes, str):
            import json
            try:
                codes = json.loads(codes)
            except Exception:
                return []
        if not codes or not codes.get("codes"):
            return []

        product_id = codes["codes"].get("product_id", "")
        if not product_id:
            return []

        # 같은 product_id를 가진 다른 캠페인 ID 조회
        other_campaigns = self._fetchall(
            """SELECT id, COALESCE(NULLIF(campaign_name, ''), product_name) AS display_name
               FROM campaigns
               WHERE id != %s AND product_codes->'codes'->>'product_id' = %s""",
            (campaign_id, product_id)
        )
        if not other_campaigns:
            return []

        other_ids = [c["id"] for c in other_campaigns]
        name_map = {c["id"]: c["display_name"] for c in other_campaigns}

        # 리뷰어의 해당 캠페인들 진행 이력
        reviewer = self.get_reviewer(name, phone)
        if not reviewer:
            return []

        rows = self._fetchall(
            """SELECT campaign_id, status FROM progress
               WHERE reviewer_id = %s AND campaign_id = ANY(%s)
               AND status NOT IN (%s, %s)""",
            (reviewer["id"], other_ids, STATUS_TIMEOUT, STATUS_CANCELLED)
        )
        return [
            {
                "campaign_id": r["campaign_id"],
                "product_name": name_map.get(r["campaign_id"], ""),
                "status": r["status"],
            }
            for r in rows
        ]

    def delete_old_cancelled_rows(self, days: int = 1) -> int:
        """신청일+N일 지난 취소/타임아웃취소 행 삭제"""
        cutoff = now_kst() - timedelta(days=days)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """DELETE FROM progress
                       WHERE status IN (%s, %s) AND created_at < %s""",
                    (STATUS_TIMEOUT, STATUS_CANCELLED, cutoff)
                )
                count = cur.rowcount
            conn.commit()
        if count:
            logger.info("취소 행 삭제: %d건 (신청일+%d일 초과)", count, days)
        return count

    def count_all_campaigns(self) -> dict:
        """캠페인별 구매완료 건수 ({캠페인ID: count})"""
        rows = self._fetchall(
            """SELECT campaign_id, COUNT(*) as cnt FROM progress
               WHERE status = ANY(%s) GROUP BY campaign_id""",
            (list(_DONE_STATUSES),)
        )
        return {r["campaign_id"]: r["cnt"] for r in rows}

    def count_reserved_campaign(self, campaign_id: str) -> int:
        """특정 캠페인의 진행중 슬롯 수 (취소 제외 전체)"""
        row = self._fetchone(
            """SELECT COUNT(*) as cnt FROM progress
               WHERE campaign_id = %s AND status NOT IN (%s, %s)""",
            (campaign_id, STATUS_TIMEOUT, STATUS_CANCELLED)
        )
        return row["cnt"] if row else 0

    # ──────── 문의 (inquiries) ────────

    def create_inquiry(self, reviewer_id: int, name: str, phone: str,
                       message: str, context: str = "", is_urgent: bool = False) -> int:
        """문의 접수. 생성된 inquiry id 반환."""
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """INSERT INTO inquiries
                       (reviewer_id, reviewer_name, reviewer_phone, message, context, is_urgent)
                       VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                    (reviewer_id, name, phone, message, context, is_urgent)
                )
                row = cur.fetchone()
            conn.commit()
        return row["id"] if row else 0

    def get_inquiries(self, status: str = None) -> list[dict]:
        """문의 목록 (최신순). status 지정 시 필터."""
        if status:
            rows = self._fetchall(
                "SELECT * FROM inquiries WHERE status = %s ORDER BY created_at DESC",
                (status,)
            )
        else:
            rows = self._fetchall("SELECT * FROM inquiries ORDER BY created_at DESC")
        for r in (rows or []):
            if r.get("created_at") and hasattr(r["created_at"], "astimezone"):
                r["created_at"] = r["created_at"].astimezone(KST)
        return rows or []

    def get_inquiry(self, inquiry_id: int) -> dict:
        """문의 단건 조회."""
        return self._fetchone("SELECT * FROM inquiries WHERE id = %s", (inquiry_id,)) or {}

    def reply_inquiry(self, inquiry_id: int, reply_text: str) -> bool:
        """문의 답변 + 상태 완료."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE inquiries SET admin_reply = %s, status = '완료',
                       replied_at = NOW() WHERE id = %s""",
                    (reply_text, inquiry_id)
                )
                ok = cur.rowcount > 0
            conn.commit()
        return ok

    def get_pending_inquiry_count(self) -> int:
        """대기 중 문의 건수."""
        row = self._fetchone("SELECT COUNT(*) as cnt FROM inquiries WHERE status = '대기'")
        return row["cnt"] if row else 0

    def get_learned_qa(self, limit: int = 30) -> list[dict]:
        """답변 완료된 문의 Q&A (AI 학습용). 최신순."""
        return self._fetchall(
            """SELECT message, admin_reply FROM inquiries
               WHERE status = '완료' AND admin_reply != ''
               ORDER BY replied_at DESC LIMIT %s""",
            (limit,)
        )

    def count_today_all_campaigns(self) -> dict:
        """오늘 캠페인별 신청 건수 ({캠페인ID: count})"""
        today = today_str()
        rows = self._fetchall(
            """SELECT campaign_id, COUNT(*) as cnt FROM progress
               WHERE created_at::date = %s::date AND status NOT IN (%s, %s)
               GROUP BY campaign_id""",
            (today, STATUS_TIMEOUT, STATUS_CANCELLED)
        )
        return {r["campaign_id"]: r["cnt"] for r in rows}

    def count_today_user_campaign(self, name: str, phone: str, campaign_id: str) -> int:
        """오늘 특정 유저의 특정 캠페인 신청 건수"""
        today = today_str()
        row = self._fetchone(
            """SELECT COUNT(*) as cnt FROM progress p
               JOIN reviewers r ON p.reviewer_id = r.id
               WHERE r.name = %s AND r.phone = %s
               AND p.campaign_id = %s
               AND p.created_at::date = %s::date
               AND p.status NOT IN (%s, %s)""",
            (name, phone, campaign_id, today, STATUS_TIMEOUT, STATUS_CANCELLED)
        )
        return row["cnt"] if row else 0

    def get_today_stats(self) -> dict:
        """오늘 현황 통계"""
        today = today_str()
        total = self._fetchone("SELECT COUNT(*) as cnt FROM progress")
        purchase = self._fetchone(
            "SELECT COUNT(*) as cnt FROM progress WHERE purchase_date = %s::date", (today,))
        review = self._fetchone(
            "SELECT COUNT(*) as cnt FROM progress WHERE review_submit_date = %s::date", (today,))
        new = self._fetchone(
            """SELECT COUNT(*) as cnt FROM progress
               WHERE status IN (%s, %s)""",
            (STATUS_GUIDE_SENT, STATUS_PURCHASE_WAIT)
        )
        return {
            "new_today": new["cnt"] if new else 0,
            "purchase_today": purchase["cnt"] if purchase else 0,
            "review_today": review["cnt"] if review else 0,
            "total": total["cnt"] if total else 0,
        }

    def get_recent_activities(self, limit: int = 30) -> list[dict]:
        """최근 활동 통합 피드 (대화 + 상태변경)"""
        try:
            return self._get_recent_activities_impl(limit)
        except Exception as e:
            logger.error(f"최근 활동 조회 에러: {e}")
            return []

    def _get_recent_activities_impl(self, limit: int) -> list[dict]:
        # 최근 대화
        chats = self._fetchall(
            """SELECT 'chat' as type, reviewer_id, sender, message,
                      created_at as ts
               FROM chat_messages
               WHERE sender = 'user'
               ORDER BY created_at DESC LIMIT %s""",
            (limit,)
        )
        # 최근 상태 변경 (신청, 취소, 구매완료, 리뷰제출 등)
        progresses = self._fetchall(
            """SELECT 'status' as type,
                      COALESCE(r.name, '') as reviewer_name,
                      COALESCE(r.phone, '') as reviewer_phone,
                      p.store_id, p.status,
                      COALESCE(NULLIF(c.campaign_name, ''), c.product_name, '') as campaign_name,
                      p.updated_at as ts
               FROM progress p
               LEFT JOIN reviewers r ON p.reviewer_id = r.id
               LEFT JOIN campaigns c ON p.campaign_id = c.id
               ORDER BY p.updated_at DESC LIMIT %s""",
            (limit,)
        )

        def _to_kst(dt):
            if dt and hasattr(dt, 'astimezone'):
                return dt.astimezone(KST)
            return dt

        activities = []
        for c in (chats or []):
            activities.append({
                "type": "chat",
                "icon": "💬",
                "who": c.get("reviewer_id", ""),
                "content": c.get("message", ""),
                "ts": _to_kst(c.get("ts")),
            })
        for p in (progresses or []):
            status = p.get("status", "")
            icon_map = {
                "신청": "📋", "가이드전달": "📤", "구매캡쳐대기": "🛒",
                "리뷰대기": "📝", "리뷰제출": "📸", "입금대기": "💰",
                "입금완료": "✅", "취소": "❌", "타임아웃취소": "⏰",
            }
            icon = icon_map.get(status, "🔄")
            name = p.get("reviewer_name", "")
            sid = p.get("store_id", "")
            camp = p.get("campaign_name", "")
            content = f"{name} ({sid}) - {camp}" if camp else f"{name} ({sid})"
            activities.append({
                "type": "status",
                "icon": icon,
                "who": f"{name}",
                "status": status,
                "content": content,
                "ts": _to_kst(p.get("ts")),
            })

        activities.sort(key=lambda x: x["ts"] if x["ts"] else "", reverse=True)
        return activities[:limit]

    # ─────────── step_machine 호환 헬퍼 ───────────

    def update_status_by_id(self, name: str, phone: str, campaign_id: str,
                             store_id: str, new_status: str):
        """진행자+캠페인+아이디로 상태 업데이트 (step_machine용)"""
        reviewer = self.get_reviewer(name, phone)
        if not reviewer:
            return
        self._execute(
            """UPDATE progress SET status = %s, updated_at = NOW()
               WHERE reviewer_id = %s AND campaign_id = %s AND store_id = %s
               AND status NOT IN (%s, %s)""",
            (new_status, reviewer["id"], campaign_id, store_id,
             STATUS_TIMEOUT, STATUS_CANCELLED)
        )

    def update_form_data(self, name: str, phone: str, campaign_id: str,
                         store_id: str, form_data: dict, campaign: dict = None):
        """양식 데이터 업데이트 (reviewer_manager.update_form_data 대체)"""
        from modules.utils import safe_int
        reviewer = self.get_reviewer(name, phone)
        if not reviewer:
            return

        camp = campaign or {}
        review_fee = safe_int(camp.get("리뷰비", 0))
        raw_payment = form_data.get("결제금액", "")
        if raw_payment:
            raw_payment = str(raw_payment).replace(",", "")
        purchase_amount = safe_int(raw_payment or camp.get("결제금액", 0))
        deposit_amount = review_fee + purchase_amount if (review_fee or purchase_amount) else 0

        # 리뷰기한 계산
        review_deadline = None
        deadline_days = safe_int(camp.get("리뷰기한일수", 0))
        if deadline_days > 0:
            review_deadline = (now_kst() + timedelta(days=deadline_days)).strftime("%Y-%m-%d")

        self._execute(
            """UPDATE progress SET
                recipient_name = %s, phone = %s, bank = %s, account = %s,
                depositor = %s, address = %s, nickname = %s,
                payment_amount = %s, review_fee = %s, payment_total = %s,
                review_deadline = %s, updated_at = NOW()
               WHERE reviewer_id = %s AND campaign_id = %s AND store_id = %s""",
            (
                form_data.get("수취인명", ""),
                form_data.get("연락처", ""),
                form_data.get("은행", ""),
                form_data.get("계좌", ""),
                form_data.get("예금주", ""),
                form_data.get("주소", ""),
                form_data.get("닉네임", ""),
                purchase_amount,
                review_fee,
                deposit_amount,
                review_deadline,
                reviewer["id"], campaign_id, store_id,
            )
        )
        logger.info("양식 업데이트: %s - %s", name, store_id)

    def get_used_store_ids(self, name: str, phone: str) -> set:
        """리뷰어가 사용한 모든 아이디 목록"""
        reviewer = self.get_reviewer(name, phone)
        if not reviewer:
            return set()
        rows = self._fetchall(
            "SELECT DISTINCT store_id FROM progress WHERE reviewer_id = %s AND store_id != ''",
            (reviewer["id"],)
        )
        return {r["store_id"] for r in rows}

    def get_active_ids_for_campaign(self, name: str, phone: str, campaign_id: str) -> set:
        """특정 캠페인에서 진행중인 아이디"""
        reviewer = self.get_reviewer(name, phone)
        if not reviewer:
            return set()
        rows = self._fetchall(
            """SELECT store_id FROM progress
               WHERE reviewer_id = %s AND campaign_id = %s
               AND status NOT IN %s AND store_id != ''""",
            (reviewer["id"], campaign_id, _DUP_IGNORE_STATUSES)
        )
        return {r["store_id"] for r in rows}

    def get_exclusive_group_active_ids(self, campaign_id: str) -> set:
        """동시진행그룹 내 다른 캠페인에서 사용 중인 아이디 목록"""
        camp = self._fetchone(
            "SELECT exclusive_group FROM campaigns WHERE id = %s", (campaign_id,)
        )
        if not camp:
            return set()
        group = (camp.get("exclusive_group") or "").strip()
        if not group:
            return set()
        rows = self._fetchall(
            """SELECT DISTINCT p.store_id
               FROM progress p
               JOIN campaigns c ON p.campaign_id = c.id
               WHERE c.exclusive_group = %s
               AND p.campaign_id != %s
               AND p.store_id != ''
               AND p.status NOT IN %s""",
            (group, campaign_id, _DUP_IGNORE_STATUSES),
        )
        return {r["store_id"] for r in rows}

    # ─────────── ensure 메서드 (시트 호환 no-op) ───────────

    def ensure_reviewer_db(self):
        """DB에선 스키마 생성에서 이미 처리됨"""
        pass

    def ensure_main_column(self, col_name: str):
        pass

    def ensure_campaign_columns(self, col_names: list):
        pass

    def ensure_campaign_column(self, col_name: str):
        pass

    # ─────────── add_reviewer_row 호환 ───────────

    def add_reviewer_row(self, data: dict):
        """시트의 add_reviewer_row 호환 → add_progress로 위임"""
        self.add_progress(data)

    # ─────────── 사이트 설정 ───────────

    def get_setting(self, key: str, default: str = "") -> str:
        """사이트 설정값 조회"""
        row = self._fetchone("SELECT value FROM site_settings WHERE key = %s", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        """사이트 설정값 저장 (upsert)"""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO site_settings (key, value, updated_at) VALUES (%s, %s, NOW())
                       ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
                    (key, value)
                )

    # ─────────── 담당자 관리 ───────────

    def get_managers(self) -> list[dict]:
        """전체 담당자 목록."""
        return self._fetchall("SELECT * FROM managers ORDER BY id")

    def get_active_managers(self) -> list[dict]:
        """카톡 수신 활성 + 현재 시간이 발송시간 내인 담당자 목록."""
        now_hm = now_kst().strftime("%H:%M")
        return self._fetchall(
            """SELECT * FROM managers
               WHERE receive_kakao = TRUE
               AND notify_start <= %s AND notify_end > %s
               ORDER BY id""",
            (now_hm, now_hm)
        )

    def add_manager(self, name: str, phone: str, role: str = "담당자") -> int:
        """담당자 추가. 중복 시 무시. id 반환."""
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """INSERT INTO managers (name, phone, role)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (name, phone) DO NOTHING
                       RETURNING id""",
                    (name, phone, role)
                )
                row = cur.fetchone()
            conn.commit()
        return row["id"] if row else 0

    def update_manager(self, manager_id: int, **kwargs):
        """담당자 정보 수정. kwargs: name, phone, role, receive_kakao, notify_start, notify_end"""
        allowed = {"name", "phone", "role", "receive_kakao", "notify_start", "notify_end"}
        sets = []
        vals = []
        for k, v in kwargs.items():
            if k in allowed:
                sets.append(f"{k} = %s")
                vals.append(v)
        if not sets:
            return
        vals.append(manager_id)
        self._execute(f"UPDATE managers SET {', '.join(sets)} WHERE id = %s", tuple(vals))

    def delete_manager(self, manager_id: int):
        """담당자 삭제."""
        self._execute("DELETE FROM managers WHERE id = %s", (manager_id,))

    # ─────────── 캠페인 상태 변경 / 삭제 ───────────

    def update_campaign_status(self, campaign_id: str, status: str):
        """캠페인 상태 변경 (모집중/중지/마감 등)"""
        self._execute(
            "UPDATE campaigns SET status = %s, updated_at = NOW() WHERE id = %s",
            (status, campaign_id)
        )

    def delete_campaign(self, campaign_id: str) -> bool:
        """캠페인 삭제. 연결된 progress의 campaign_id는 NULL로 설정됨 (ON DELETE SET NULL)."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM campaigns WHERE id = %s", (campaign_id,))
                ok = cur.rowcount > 0
            conn.commit()
        return ok

    # ──────── 대화이력 (chat_messages) ────────

    def save_chat_message(self, reviewer_id: str, sender: str, message: str, rating: str = ""):
        """대화 메시지 DB 저장 (즉시)"""
        self._execute(
            """INSERT INTO chat_messages (reviewer_id, sender, message, rating)
               VALUES (%s, %s, %s, %s)""",
            (reviewer_id, sender, message, rating)
        )

    def get_chat_history(self, reviewer_id: str) -> list[dict]:
        """특정 리뷰어 대화 이력 (최근 90일)"""
        return self._fetchall(
            """SELECT sender, message, EXTRACT(EPOCH FROM created_at) as timestamp, rating
               FROM chat_messages
               WHERE reviewer_id = %s AND created_at > NOW() - INTERVAL '90 days'
               ORDER BY created_at""",
            (reviewer_id,)
        )

    def count_chat_unread(self, reviewer_id: str, last_read_ts: float) -> int:
        """last_read_ts 이후의 bot 메시지 수"""
        row = self._fetchone(
            """SELECT COUNT(*) as cnt FROM chat_messages
               WHERE reviewer_id = %s AND sender = 'bot'
                 AND created_at > to_timestamp(%s)""",
            (reviewer_id, last_read_ts)
        )
        return int(row["cnt"]) if row else 0

    def get_chat_reviewer_ids(self) -> list[str]:
        """대화 기록이 있는 리뷰어 ID 목록 (최근 대화순)"""
        rows = self._fetchall(
            "SELECT reviewer_id, MAX(created_at) as last_msg FROM chat_messages GROUP BY reviewer_id ORDER BY last_msg DESC"
        )
        return [r["reviewer_id"] for r in rows]

    def get_recent_chat_messages(self, limit: int = 20) -> list[dict]:
        """최근 대화 메시지 (관리자 대시보드용)"""
        return self._fetchall(
            """SELECT reviewer_id, sender, message,
                      EXTRACT(EPOCH FROM created_at) as timestamp
               FROM chat_messages ORDER BY created_at DESC LIMIT %s""",
            (limit,)
        )

    def search_chat_messages(self, keyword: str) -> list[dict]:
        """대화 메시지 검색"""
        return self._fetchall(
            """SELECT reviewer_id, sender, message,
                      EXTRACT(EPOCH FROM created_at) as timestamp
               FROM chat_messages
               WHERE message ILIKE %s OR reviewer_id ILIKE %s
               ORDER BY created_at DESC LIMIT 200""",
            (f"%{keyword}%", f"%{keyword}%")
        )

    def rate_chat_message(self, reviewer_id: str, timestamp: float, rating: str) -> bool:
        """대화 메시지 평가"""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE chat_messages SET rating = %s
                       WHERE id = (
                           SELECT id FROM chat_messages
                           WHERE reviewer_id = %s
                           AND ABS(EXTRACT(EPOCH FROM created_at) - %s) < 1
                           LIMIT 1
                       )""",
                    (rating, reviewer_id, timestamp)
                )
                ok = cur.rowcount > 0
            conn.commit()
        return ok

    def cleanup_old_chat(self, days: int = 90) -> int:
        """오래된 대화 삭제"""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM chat_messages WHERE created_at < NOW() - make_interval(days => %s)",
                    (days,)
                )
                count = cur.rowcount
            conn.commit()
        return count

    # ─────────── suppliers (공급자 프리셋) ───────────

    def create_supplier(self, data: dict) -> int:
        return self._execute_returning(
            """INSERT INTO suppliers (name, biz_number, company_name, ceo_name, address,
               biz_type, biz_category, bank_account, manager_name, manager_phone, is_default)
               VALUES (%(name)s, %(biz_number)s, %(company_name)s, %(ceo_name)s, %(address)s,
               %(biz_type)s, %(biz_category)s, %(bank_account)s, %(manager_name)s, %(manager_phone)s,
               %(is_default)s) RETURNING id""",
            {
                "name": data.get("name", ""),
                "biz_number": data.get("biz_number", ""),
                "company_name": data.get("company_name", ""),
                "ceo_name": data.get("ceo_name", ""),
                "address": data.get("address", ""),
                "biz_type": data.get("biz_type", ""),
                "biz_category": data.get("biz_category", ""),
                "bank_account": data.get("bank_account", ""),
                "manager_name": data.get("manager_name", ""),
                "manager_phone": data.get("manager_phone", ""),
                "is_default": data.get("is_default", False),
            },
        )

    def get_suppliers(self) -> list[dict]:
        return self._fetchall("SELECT * FROM suppliers ORDER BY is_default DESC, id")

    def get_supplier(self, sid: int) -> dict | None:
        return self._fetchone("SELECT * FROM suppliers WHERE id = %s", (sid,))

    def get_default_supplier(self) -> dict | None:
        return self._fetchone("SELECT * FROM suppliers WHERE is_default = TRUE LIMIT 1")

    def update_supplier(self, sid: int, data: dict):
        cols = ["name", "biz_number", "company_name", "ceo_name", "address",
                "biz_type", "biz_category", "bank_account", "manager_name", "manager_phone"]
        sets, params = [], []
        for c in cols:
            if c in data:
                sets.append(f"{c} = %s")
                params.append(data[c])
        if not sets:
            return
        params.append(sid)
        self._execute(f"UPDATE suppliers SET {', '.join(sets)} WHERE id = %s", params)

    def set_default_supplier(self, sid: int):
        self._execute("UPDATE suppliers SET is_default = FALSE WHERE is_default = TRUE")
        self._execute("UPDATE suppliers SET is_default = TRUE WHERE id = %s", (sid,))

    def delete_supplier(self, sid: int):
        self._execute("DELETE FROM suppliers WHERE id = %s", (sid,))

    # ─────────── quotes (견적서) ───────────

    def create_quote(self, raw_text: str, parsed_data: dict, status: str = "확인대기",
                     supplier_id: int | None = None, recipient: str = "",
                     items: list | None = None, notes: str = "") -> int:
        import json
        return self._execute_returning(
            """INSERT INTO quotes (raw_text, parsed_data, status, supplier_id, recipient, items, notes)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (raw_text, json.dumps(parsed_data, ensure_ascii=False), status,
             supplier_id, recipient,
             json.dumps(items or [], ensure_ascii=False), notes),
        )

    def get_quotes(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self._fetchall(
                "SELECT * FROM quotes WHERE status = %s ORDER BY created_at DESC", (status,)
            )
        else:
            rows = self._fetchall("SELECT * FROM quotes ORDER BY created_at DESC")
        for r in (rows or []):
            if r.get("created_at") and hasattr(r["created_at"], "astimezone"):
                r["created_at"] = r["created_at"].astimezone(KST)
        return rows or []

    def get_quote(self, quote_id: int) -> dict | None:
        return self._fetchone("SELECT * FROM quotes WHERE id = %s", (quote_id,))

    def update_quote(self, quote_id: int, **kwargs):
        import json
        sets, params = [], []
        for key in ("status", "memo", "recipient", "notes"):
            if key in kwargs and kwargs[key] is not None:
                sets.append(f"{key} = %s")
                params.append(kwargs[key])
        if "parsed_data" in kwargs and kwargs["parsed_data"] is not None:
            sets.append("parsed_data = %s")
            params.append(json.dumps(kwargs["parsed_data"], ensure_ascii=False))
        if "items" in kwargs and kwargs["items"] is not None:
            sets.append("items = %s")
            params.append(json.dumps(kwargs["items"], ensure_ascii=False))
        if "supplier_id" in kwargs:
            sets.append("supplier_id = %s")
            params.append(kwargs["supplier_id"])
        if not sets:
            return
        sets.append("updated_at = NOW()")
        params.append(quote_id)
        self._execute(f"UPDATE quotes SET {', '.join(sets)} WHERE id = %s", params)

    def approve_quote(self, quote_id: int, campaign_id: str):
        self._execute(
            "UPDATE quotes SET status = '승인', campaign_id = %s, updated_at = NOW() WHERE id = %s",
            (campaign_id, quote_id),
        )

    def delete_quote(self, quote_id: int):
        self._execute("DELETE FROM quotes WHERE id = %s", (quote_id,))

    # ─────────── 캠페인 사진 세트 ───────────

    def add_campaign_photo(self, campaign_id: str, set_number: int, file_index: int,
                           drive_url: str, filename: str = ""):
        self._execute(
            """INSERT INTO campaign_photos (campaign_id, set_number, file_index, drive_url, filename)
               VALUES (%s, %s, %s, %s, %s)""",
            (campaign_id, set_number, file_index, drive_url, filename),
        )

    def get_campaign_photo_sets(self, campaign_id: str) -> dict:
        """캠페인 사진 세트 반환. {1: [{url, filename, file_index}], 2: [...], ...}"""
        rows = self._fetchall(
            "SELECT * FROM campaign_photos WHERE campaign_id = %s ORDER BY set_number, file_index",
            (campaign_id,),
        )
        sets: dict[int, list] = {}
        for r in rows:
            sn = r["set_number"]
            sets.setdefault(sn, []).append({
                "url": r["drive_url"],
                "filename": r["filename"],
                "file_index": r["file_index"],
            })
        return sets

    def delete_campaign_photos(self, campaign_id: str):
        self._execute("DELETE FROM campaign_photos WHERE campaign_id = %s", (campaign_id,))

    def get_next_photo_set_number(self, campaign_id: str) -> int | None:
        """미할당된 가장 작은 세트 번호 반환. 없으면 None."""
        row = self._fetchone(
            """SELECT cp.set_number FROM campaign_photos cp
               WHERE cp.campaign_id = %s
               AND cp.set_number NOT IN (
                   SELECT DISTINCT photo_set_number FROM progress
                   WHERE campaign_id = %s AND photo_set_number IS NOT NULL
                   AND status NOT IN ('타임아웃취소', '취소')
               )
               GROUP BY cp.set_number
               ORDER BY cp.set_number
               LIMIT 1""",
            (campaign_id, campaign_id),
        )
        return row["set_number"] if row else None

    def assign_photo_set(self, progress_ids: list[int], set_number: int):
        """progress 목록에 사진 세트 번호 할당"""
        if not progress_ids:
            return
        placeholders = ", ".join(["%s"] * len(progress_ids))
        self._execute(
            f"UPDATE progress SET photo_set_number = %s WHERE id IN ({placeholders})",
            [set_number] + progress_ids,
        )

    def get_unassigned_active_progress(self, campaign_id: str) -> list[dict]:
        """사진 세트 미할당 + 활성 상태 진행건 (계정/progress 단위)"""
        rows = self._fetchall(
            """SELECT p.id, p.reviewer_id, p.status, p.store_id, p.recipient_name,
                      r.name, r.phone
               FROM progress p
               JOIN reviewers r ON p.reviewer_id = r.id
               WHERE p.campaign_id = %s
               AND p.photo_set_number IS NULL
               AND p.status NOT IN ('타임아웃취소', '취소', '입금완료')
               ORDER BY p.created_at""",
            (campaign_id,),
        )
        return [
            {
                "progress_id": r["id"],
                "reviewer_id": r["reviewer_id"],
                "name": r["name"],
                "phone": r["phone"],
                "status": r["status"],
                "store_id": r.get("store_id", ""),
                "recipient_name": r.get("recipient_name", ""),
            }
            for r in rows
        ]

    def get_photo_set_assignments(self, campaign_id: str) -> dict:
        """세트별 할당 현황. {set_number: {"name": str, "phone": str} | None}"""
        photo_sets = self.get_campaign_photo_sets(campaign_id)
        if not photo_sets:
            return {}
        rows = self._fetchall(
            """SELECT DISTINCT p.photo_set_number, r.name, r.phone
               FROM progress p
               JOIN reviewers r ON p.reviewer_id = r.id
               WHERE p.campaign_id = %s AND p.photo_set_number IS NOT NULL
               AND p.status NOT IN ('타임아웃취소', '취소')""",
            (campaign_id,),
        )
        assigned = {}
        for r in rows:
            assigned[r["photo_set_number"]] = {"name": r["name"], "phone": r["phone"]}
        return {sn: assigned.get(sn) for sn in photo_sets}

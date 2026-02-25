"""
sheet_sync.py - DB → Google Sheets 증분 동기화

progress(진행건) 데이터를 1분 주기로 구글 시트에 증분 동기화.
- 첫 실행: 전체 동기화
- 이후: 추가/수정/삭제만 반영 (API 호출 최소화)
- 30분마다: 전체 재동기화 (안전장치)
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

import gspread

logger = logging.getLogger(__name__)

DEFAULT_SYNC_SPREADSHEET_ID = "1NaUN2_pK-m7gR2Ywj8JvPLA2X1B7gO7SYzpvm6svz_0"

SHEET_HEADERS = [
    "ID", "업체명", "날짜", "제품명", "수취인명", "연락처",
    "은행", "계좌", "예금주", "결제금액", "아이디", "주문번호",
    "주소", "닉네임", "회수이름", "회수연락처", "리뷰작성",
    "리뷰비", "입금금액", "", "", "구매캡쳐", "리뷰캡쳐", "상태",
]

_NUM_COLS = len(SHEET_HEADERS)  # 24
_LAST_COL = "X"

# progress + campaign + reviewer JOIN 쿼리
_SYNC_SQL = """
    SELECT
        p.id,
        c.company,
        p.created_at,
        COALESCE(NULLIF(c.campaign_name, ''), c.product_name) AS product_name,
        p.recipient_name,
        p.phone,
        p.bank,
        p.account,
        p.depositor,
        p.payment_amount,
        p.store_id,
        p.order_number,
        p.address,
        p.nickname,
        r.name   AS reviewer_name,
        r.phone  AS reviewer_phone,
        p.review_submit_date,
        p.review_fee,
        p.payment_total,
        p.status,
        p.purchase_capture_url,
        p.review_capture_url,
        p.updated_at
    FROM progress p
    LEFT JOIN campaigns c ON p.campaign_id = c.id
    LEFT JOIN reviewers r ON p.reviewer_id = r.id
    ORDER BY p.id
"""

FULL_SYNC_EVERY = 360  # 360 사이클(=6시간)마다 전체 재동기화

# 변경분만 가져오는 쿼리
_SYNC_CHANGED_SQL = _SYNC_SQL.replace("ORDER BY p.id", "WHERE (p.updated_at > %s OR p.created_at > %s) ORDER BY p.id")

# ID만 가져오는 경량 쿼리 (삭제 감지용)
_SYNC_IDS_SQL = "SELECT id FROM progress ORDER BY id"


class SheetSync:
    """DB → Google Sheets 증분 동기화"""

    def __init__(self, db_manager, gc: gspread.Client):
        self.db = db_manager
        self.gc = gc
        self.spreadsheet = None
        self.worksheet = None
        self._stop_event = threading.Event()
        self._thread = None

        # 증분 동기화 상태
        self._id_row_map: dict[int, int] = {}  # {progress_id: sheet_row_number}
        self._last_sync_utc: datetime | None = None
        self._sync_count = 0

        self._init_spreadsheet()

    def _init_spreadsheet(self):
        spreadsheet_id = os.environ.get(
            "SYNC_SPREADSHEET_ID", DEFAULT_SYNC_SPREADSHEET_ID
        )
        self.spreadsheet = self.gc.open_by_key(spreadsheet_id)
        self.worksheet = self.spreadsheet.sheet1
        logger.info("시트 동기화 준비 완료 (URL: %s)", self.spreadsheet.url)

    @staticmethod
    def _build_row(r: dict) -> list:
        """DB row dict → sheet row list"""
        review_status = ""
        if r.get("review_submit_date"):
            review_status = str(r["review_submit_date"])
        elif r.get("status") in ("리뷰대기",):
            review_status = "대기"

        return [
            r.get("id", 0),                                                # ID
            r.get("company") or "",                                        # 업체명
            r["created_at"].strftime("%Y-%m-%d") if r.get("created_at") else "",  # 날짜
            r.get("product_name") or "",                                   # 제품명
            r.get("recipient_name") or "",                                 # 수취인명
            r.get("phone") or "",                                          # 연락처
            r.get("bank") or "",                                           # 은행
            r.get("account") or "",                                        # 계좌
            r.get("depositor") or "",                                      # 예금주
            str(r.get("payment_amount") or 0),                             # 결제금액
            r.get("store_id") or "",                                       # 아이디
            r.get("order_number") or "",                                   # 주문번호
            r.get("address") or "",                                        # 주소
            r.get("nickname") or "",                                       # 닉네임
            r.get("reviewer_name") or "",                                  # 회수이름
            r.get("reviewer_phone") or "",                                 # 회수연락처
            review_status,                                                 # 리뷰작성
            str(r.get("review_fee") or 0),                                 # 리뷰비
            str(r.get("payment_total") or 0),                              # 입금금액 (S)
            "",                                                            # T (빈칸)
            "",                                                            # U (빈칸)
            r.get("purchase_capture_url") or "",                           # 구매캡쳐 (V)
            r.get("review_capture_url") or "",                             # 리뷰캡쳐 (W)
            r.get("status") or "",                                         # 상태 (X)
        ]

    # ──────── 전체 동기화 ────────

    def _full_sync(self):
        """전체 데이터 덮어쓰기 (초기 or 주기적)"""
        rows = self.db._fetchall(_SYNC_SQL)

        data = [SHEET_HEADERS]
        self._id_row_map = {}
        for idx, r in enumerate(rows):
            sheet_row = idx + 2  # row 1 = header
            self._id_row_map[r["id"]] = sheet_row
            data.append(self._build_row(r))

        needed_rows = len(data) + 10
        current_rows = self.worksheet.row_count
        if current_rows < needed_rows:
            self.worksheet.resize(rows=needed_rows, cols=_NUM_COLS)

        # clear 없이 덮어쓰기 (깜빡임 방지)
        if data:
            self.worksheet.update(data, "A1")

        # 기존 데이터가 더 많았으면 남은 행 삭제
        extra_start = len(data) + 1
        if current_rows >= extra_start + 1:
            self.worksheet.batch_clear([f"A{extra_start}:{_LAST_COL}{current_rows}"])

        self.worksheet.format(f"A1:{_LAST_COL}1", {
            "textFormat": {"bold": True},
            "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
        })

        self._last_sync_utc = datetime.now(timezone.utc)
        logger.info("전체 동기화 완료: %d건", len(rows))

    # ──────── 증분 동기화 ────────

    def _incremental_sync(self):
        """변경분만 DB에서 가져와서 부분 수정"""
        sheet_ids = set(self._id_row_map.keys())

        # 1) 삭제 감지: ID만 경량 조회
        current_ids_rows = self.db._fetchall(_SYNC_IDS_SQL)
        db_ids = {r["id"] for r in current_ids_rows}
        deleted_ids = sheet_ids - db_ids

        # 2) 변경/추가분: updated_at > last_sync 인 것만 조회
        changed_rows = []
        if self._last_sync_utc:
            changed_rows = self.db._fetchall(
                _SYNC_CHANGED_SQL, (self._last_sync_utc, self._last_sync_utc)
            )
        changed_map = {r["id"]: r for r in changed_rows}

        new_ids = {pid for pid in changed_map if pid not in sheet_ids}
        updated_ids = {pid for pid in changed_map if pid in sheet_ids}

        if not new_ids and not deleted_ids and not updated_ids:
            return

        # 1) 삭제 (하단→상단 순서로 삭제해야 인덱스 안 꼬임)
        if deleted_ids:
            del_rows_asc = sorted([self._id_row_map[pid] for pid in deleted_ids])

            requests = []
            for row_num in reversed(del_rows_asc):
                requests.append({
                    "deleteDimension": {
                        "range": {
                            "sheetId": self.worksheet.id,
                            "dimension": "ROWS",
                            "startIndex": row_num - 1,
                            "endIndex": row_num,
                        }
                    }
                })
            if requests:
                self.spreadsheet.batch_update({"requests": requests})

            for pid in deleted_ids:
                del self._id_row_map[pid]
            for pid in list(self._id_row_map):
                original = self._id_row_map[pid]
                shift = sum(1 for dr in del_rows_asc if dr < original)
                self._id_row_map[pid] = original - shift

        # 2) 수정 (batch update)
        if updated_ids:
            batch = []
            for pid in updated_ids:
                if pid in self._id_row_map:
                    row_num = self._id_row_map[pid]
                    row_data = self._build_row(changed_map[pid])
                    batch.append({
                        "range": f"A{row_num}:{_LAST_COL}{row_num}",
                        "values": [row_data],
                    })
            if batch:
                self.worksheet.batch_update(batch)

        # 3) 추가 (append)
        if new_ids:
            next_row = max(self._id_row_map.values(), default=1) + 1
            new_data = []
            for pid in sorted(new_ids):
                new_data.append(self._build_row(changed_map[pid]))
                self._id_row_map[pid] = next_row
                next_row += 1

            needed = next_row + 10
            if self.worksheet.row_count < needed:
                self.worksheet.resize(rows=needed, cols=_NUM_COLS)

            self.worksheet.append_rows(new_data, value_input_option="RAW")

        self._last_sync_utc = datetime.now(timezone.utc)
        logger.info(
            "증분 동기화: 추가=%d 수정=%d 삭제=%d",
            len(new_ids), len(updated_ids), len(deleted_ids),
        )

    # ──────── 메인 sync ────────

    def sync(self):
        """동기화 실행 (초기=전체, 이후=증분, 30분마다=전체)"""
        try:
            self._sync_count += 1
            if not self._id_row_map or self._sync_count % FULL_SYNC_EVERY == 0:
                self._full_sync()
            else:
                self._incremental_sync()
        except Exception as e:
            logger.error("시트 동기화 실패: %s", e, exc_info=True)
            # 다음 사이클에 전체 재동기화
            self._id_row_map = {}

    def start_background_sync(self, interval: int = 60):
        if self._thread and self._thread.is_alive():
            logger.warning("백그라운드 동기화 이미 실행 중")
            return
        self._stop_event.clear()

        def _loop():
            logger.info("시트 동기화 스레드 시작 (주기: %d초)", interval)
            self.sync()
            while not self._stop_event.is_set():
                self._stop_event.wait(interval)
                if not self._stop_event.is_set():
                    self.sync()
            logger.info("시트 동기화 스레드 종료")

        self._thread = threading.Thread(target=_loop, daemon=True, name="sheet-sync")
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
            logger.info("시트 동기화 중지됨")

    @property
    def spreadsheet_url(self) -> str:
        return self.spreadsheet.url if self.spreadsheet else ""

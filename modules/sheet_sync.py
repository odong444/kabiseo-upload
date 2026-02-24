"""
sheet_sync.py - DB → Google Sheets 자동 동기화

progress(진행건) 데이터를 1분 주기로 구글 시트에 동기화.
사용자가 직접 만든 스프레드시트(SYNC_SPREADSHEET_ID)를 열어서
첫 번째 워크시트에 전체 덮어쓰기 방식으로 동기화.
"""

import logging
import os
import threading
import time

import gspread

logger = logging.getLogger(__name__)

# 기본 동기화 대상 스프레드시트 ID (사용자 생성)
DEFAULT_SYNC_SPREADSHEET_ID = "1NaUN2_pK-m7gR2Ywj8JvPLA2X1B7gO7SYzpvm6svz_0"

# 시트 헤더 (메인_외부 양식과 동일 + 상태 컬럼)
SHEET_HEADERS = [
    "순번", "업체명", "날짜", "제품명", "수취인명", "연락처",
    "은행", "계좌", "예금주", "결제금액", "아이디", "주문번호",
    "주소", "닉네임", "회수이름", "회수연락처", "리뷰작성",
    "리뷰비", "입금금액", "상태",
]

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
        p.status
    FROM progress p
    LEFT JOIN campaigns c ON p.campaign_id = c.id
    LEFT JOIN reviewers r ON p.reviewer_id = r.id
    ORDER BY p.created_at DESC
"""


class SheetSync:
    """DB → Google Sheets 단방향 동기화"""

    def __init__(self, db_manager, gc: gspread.Client):
        """
        Args:
            db_manager: DBManager 인스턴스 (_fetchall 메서드 사용)
            gc: gspread.Client (서비스 계정)
        """
        self.db = db_manager
        self.gc = gc
        self.spreadsheet = None
        self.worksheet = None
        self._stop_event = threading.Event()
        self._thread = None

        self._init_spreadsheet()

    def _init_spreadsheet(self):
        """환경변수 SYNC_SPREADSHEET_ID로 기존 스프레드시트 열기"""
        spreadsheet_id = os.environ.get(
            "SYNC_SPREADSHEET_ID", DEFAULT_SYNC_SPREADSHEET_ID
        )

        self.spreadsheet = self.gc.open_by_key(spreadsheet_id)
        logger.info(
            "동기화 스프레드시트 열기: %s (ID: %s)",
            self.spreadsheet.title,
            spreadsheet_id,
        )

        # 첫 번째 워크시트 사용
        self.worksheet = self.spreadsheet.sheet1

        logger.info("시트 동기화 준비 완료 (URL: %s)", self.spreadsheet.url)

    def sync(self):
        """DB에서 전체 progress 조회 → 시트에 덮어쓰기"""
        try:
            rows = self.db._fetchall(_SYNC_SQL)
            # 데이터 매핑
            data = [SHEET_HEADERS]  # 첫 행 = 헤더
            for idx, r in enumerate(rows, start=1):
                # 리뷰작성 상태 판단
                review_status = ""
                if r.get("review_submit_date"):
                    review_status = str(r["review_submit_date"])
                elif r.get("status") in ("리뷰대기",):
                    review_status = "대기"

                row = [
                    idx,                                                    # 순번
                    r.get("company") or "",                                 # 업체명
                    r["created_at"].strftime("%Y-%m-%d") if r.get("created_at") else "",  # 날짜
                    r.get("product_name") or "",                            # 제품명
                    r.get("recipient_name") or "",                          # 수취인명
                    r.get("phone") or "",                                   # 연락처
                    r.get("bank") or "",                                    # 은행
                    r.get("account") or "",                                 # 계좌
                    r.get("depositor") or "",                               # 예금주
                    str(r.get("payment_amount") or 0),                      # 결제금액
                    r.get("store_id") or "",                                # 아이디
                    r.get("order_number") or "",                            # 주문번호
                    r.get("address") or "",                                 # 주소
                    r.get("nickname") or "",                                # 닉네임
                    r.get("reviewer_name") or "",                           # 회수이름
                    r.get("reviewer_phone") or "",                          # 회수연락처
                    review_status,                                          # 리뷰작성
                    str(r.get("review_fee") or 0),                          # 리뷰비
                    str(r.get("payment_total") or 0),                       # 입금금액
                    r.get("status") or "",                                  # 상태
                ]
                data.append(row)

            # 시트 크기 조정 (데이터 행 수 + 여유)
            needed_rows = len(data) + 10
            needed_cols = len(SHEET_HEADERS)
            if self.worksheet.row_count < needed_rows:
                self.worksheet.resize(rows=needed_rows, cols=needed_cols)

            # 전체 덮어쓰기: clear → update
            self.worksheet.clear()
            if data:
                self.worksheet.update(data, "A1")

            # 헤더 행 굵게 표시
            self.worksheet.format("A1:T1", {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
            })

            logger.info("시트 동기화 완료: %d건", len(data) - 1)

        except Exception as e:
            logger.error("시트 동기화 실패: %s", e, exc_info=True)

    def start_background_sync(self, interval: int = 60):
        """백그라운드 스레드에서 interval 초마다 sync() 호출"""
        if self._thread and self._thread.is_alive():
            logger.warning("백그라운드 동기화 이미 실행 중")
            return

        self._stop_event.clear()

        def _loop():
            logger.info("시트 동기화 백그라운드 스레드 시작 (주기: %d초)", interval)
            # 첫 실행
            self.sync()
            while not self._stop_event.is_set():
                self._stop_event.wait(interval)
                if not self._stop_event.is_set():
                    self.sync()
            logger.info("시트 동기화 백그라운드 스레드 종료")

        self._thread = threading.Thread(target=_loop, daemon=True, name="sheet-sync")
        self._thread.start()

    def stop(self):
        """백그라운드 동기화 중지"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
            logger.info("시트 동기화 중지됨")

    @property
    def spreadsheet_url(self) -> str:
        """스프레드시트 URL"""
        return self.spreadsheet.url if self.spreadsheet else ""

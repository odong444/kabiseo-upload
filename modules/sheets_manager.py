"""
sheets_manager.py - Google Sheets CRUD (6탭 지원)

실제 시트 컬럼:
카비서_정리: 회수여부, 캠페인ID, 업체명, 날짜, 제품명, 수취인명, 연락처,
  은행, 계좌, 예금주, 결제금액, 아이디, 주문번호, 주소, 닉네임, 진행자연락처,
  상태, 구매일, 구매캡쳐링크, 리뷰기한, 리뷰제출일, 리뷰캡쳐링크,
  리뷰비, 입금금액, 입금정리, 비고

캠페인관리: 캠페인ID, 등록일, 상태, 업체명, 상품명, 상품링크, 옵션, 키워드,
  현재순위, 유입방식, 총수량, 일수량, 완료수량, 당일발송, 발송마감,
  일최대건, 택배사, 3PL사용, 3PL비용, 주말작업, 리뷰제공, 리뷰기한일수, 메모
"""

import logging
from modules.utils import today_str

logger = logging.getLogger(__name__)

# 상태 상수
STATUS_GUIDE_SENT = "가이드전달"
STATUS_FORM_RECEIVED = "양식접수"
STATUS_REVIEW_WAIT = "리뷰대기"
STATUS_REVIEW_DONE = "리뷰완료"
STATUS_SETTLED = "정산완료"
STATUS_CANCELLED = "취소"


class SheetsManager:
    """구글시트 CRUD 매니저"""

    MAIN_SHEET = "카비서_정리"

    def __init__(self, gspread_client, spreadsheet_id: str):
        self.client = gspread_client
        self.spreadsheet_id = spreadsheet_id
        self._spreadsheet = None

    @property
    def spreadsheet(self):
        if self._spreadsheet is None:
            self._spreadsheet = self.client.open_by_key(self.spreadsheet_id)
        return self._spreadsheet

    def _get_ws(self, sheet_name: str = None):
        name = sheet_name or self.MAIN_SHEET
        return self.spreadsheet.worksheet(name)

    def _get_headers(self, ws):
        return ws.row_values(1)

    def _find_col(self, headers, col_name):
        if col_name in headers:
            return headers.index(col_name)
        return -1

    # ──────────── 검색 ────────────

    def search_by_name_phone(self, name: str, phone: str) -> list[dict]:
        """이름+연락처로 전체 건 검색"""
        ws = self._get_ws()
        headers = self._get_headers(ws)
        all_rows = ws.get_all_values()

        name_col = self._find_col(headers, "수취인명")
        phone_col = self._find_col(headers, "연락처")
        if name_col < 0 or phone_col < 0:
            return []

        results = []
        for i, row in enumerate(all_rows[1:], start=2):
            if len(row) <= max(name_col, phone_col):
                continue
            if row[name_col] == name and row[phone_col] == phone:
                row_dict = {headers[j]: row[j] for j in range(len(headers)) if j < len(row)}
                row_dict["_row_idx"] = i
                results.append(row_dict)
        return results

    def search_by_depositor(self, capture_type: str, name: str) -> list[dict]:
        """예금주명으로 검색 (기존 호환)"""
        ws = self._get_ws()
        headers = self._get_headers(ws)
        all_rows = ws.get_all_values()

        depositor_col = self._find_col(headers, "예금주")
        status_col = self._find_col(headers, "상태")
        if depositor_col < 0 or status_col < 0:
            return []

        target_status = STATUS_FORM_RECEIVED if capture_type == "purchase" else STATUS_REVIEW_WAIT

        results = []
        for i, row in enumerate(all_rows[1:], start=2):
            if len(row) <= max(depositor_col, status_col):
                continue
            if row[depositor_col] == name and row[status_col] == target_status:
                row_dict = {headers[j]: row[j] for j in range(len(headers)) if j < len(row)}
                row_dict["_row_idx"] = i
                results.append(row_dict)
        return results

    def search_by_name_phone_or_depositor(self, capture_type: str, query: str, phone: str = "") -> list[dict]:
        """이름+연락처 또는 예금주로 검색"""
        ws = self._get_ws()
        headers = self._get_headers(ws)
        all_rows = ws.get_all_values()

        name_col = self._find_col(headers, "수취인명")
        phone_col = self._find_col(headers, "연락처")
        depositor_col = self._find_col(headers, "예금주")
        status_col = self._find_col(headers, "상태")

        if status_col < 0:
            return []

        target_status = STATUS_FORM_RECEIVED if capture_type == "purchase" else STATUS_REVIEW_WAIT

        results = []
        for i, row in enumerate(all_rows[1:], start=2):
            if len(row) <= status_col:
                continue
            if row[status_col] != target_status:
                continue

            matched = False
            # 이름+연락처 매칭
            if name_col >= 0 and phone_col >= 0 and phone:
                if len(row) > max(name_col, phone_col):
                    if row[name_col] == query and row[phone_col] == phone:
                        matched = True
            # 예금주 매칭
            if not matched and depositor_col >= 0 and len(row) > depositor_col:
                if row[depositor_col] == query:
                    matched = True

            if matched:
                row_dict = {headers[j]: row[j] for j in range(len(headers)) if j < len(row)}
                row_dict["_row_idx"] = i
                results.append(row_dict)
        return results

    def get_reviewer_items(self, name: str, phone: str) -> dict:
        """리뷰어의 진행현황: 진행중/완료 분류"""
        all_items = self.search_by_name_phone(name, phone)
        in_progress = []
        completed = []
        for item in all_items:
            status = item.get("상태", "")
            if status in (STATUS_SETTLED, STATUS_REVIEW_DONE):
                completed.append(item)
            elif status == STATUS_CANCELLED:
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
            elif status in (STATUS_FORM_RECEIVED, STATUS_GUIDE_SENT, STATUS_REVIEW_WAIT):
                if status == STATUS_REVIEW_WAIT:
                    no_review.append(item)

        return {"paid": paid, "pending": pending, "no_review": no_review}

    # ──────────── 업데이트 ────────────

    def update_cell_by_col(self, row_idx: int, col_name: str, value: str):
        """특정 행의 특정 열 업데이트"""
        ws = self._get_ws()
        headers = self._get_headers(ws)
        col = self._find_col(headers, col_name)
        if col >= 0:
            ws.update_cell(row_idx, col + 1, value)

    def update_after_upload(self, capture_type: str, row_idx: int, drive_link: str):
        """업로드 완료 후 시트 업데이트"""
        if capture_type == "purchase":
            self.update_cell_by_col(row_idx, "구매캡쳐링크", drive_link)
            self.update_cell_by_col(row_idx, "상태", STATUS_REVIEW_WAIT)
        elif capture_type == "review":
            self.update_cell_by_col(row_idx, "리뷰캡쳐링크", drive_link)
            self.update_cell_by_col(row_idx, "상태", STATUS_REVIEW_DONE)
            self.update_cell_by_col(row_idx, "리뷰제출일", today_str())

    def update_status(self, row_idx: int, status: str):
        self.update_cell_by_col(row_idx, "상태", status)

    def add_reviewer_row(self, data: dict):
        """새 리뷰어 행 추가"""
        ws = self._get_ws()
        headers = self._get_headers(ws)
        new_row = []
        for h in headers:
            new_row.append(data.get(h, ""))
        ws.append_row(new_row, value_input_option="USER_ENTERED")

    def process_settlement(self, row_idx: int, amount: str):
        """정산 처리"""
        self.update_cell_by_col(row_idx, "상태", STATUS_SETTLED)
        self.update_cell_by_col(row_idx, "입금금액", amount)
        self.update_cell_by_col(row_idx, "입금정리", today_str())

    # ──────────── 캠페인 ────────────

    def get_all_campaigns(self) -> list[dict]:
        """캠페인관리 시트에서 전체 목록 조회"""
        try:
            ws = self.spreadsheet.worksheet("캠페인관리")
        except Exception:
            return []

        headers = self._get_headers(ws)
        all_rows = ws.get_all_values()
        results = []
        for i, row in enumerate(all_rows[1:], start=2):
            row_dict = {headers[j]: row[j] for j in range(len(headers)) if j < len(row)}
            row_dict["_row_idx"] = i
            results.append(row_dict)
        return results

    def get_campaign_by_id(self, campaign_id: str) -> dict | None:
        campaigns = self.get_all_campaigns()
        for c in campaigns:
            if c.get("캠페인ID", "") == campaign_id:
                return c
        return None

    def check_duplicate(self, campaign_id: str, store_id: str) -> bool:
        """같은 캠페인ID + 같은 아이디 중복 여부 확인"""
        ws = self._get_ws()
        headers = self._get_headers(ws)
        all_rows = ws.get_all_values()

        cid_col = self._find_col(headers, "캠페인ID")
        sid_col = self._find_col(headers, "아이디")
        status_col = self._find_col(headers, "상태")
        if cid_col < 0 or sid_col < 0:
            return False

        for row in all_rows[1:]:
            if len(row) <= max(cid_col, sid_col):
                continue
            if row[cid_col] == campaign_id and row[sid_col] == store_id:
                # 취소 상태는 제외
                if status_col >= 0 and len(row) > status_col and row[status_col] == STATUS_CANCELLED:
                    continue
                return True
        return False

    def update_campaign_cell(self, row_idx: int, col_name: str, value: str):
        """캠페인관리 시트의 특정 셀 업데이트"""
        try:
            ws = self.spreadsheet.worksheet("캠페인관리")
            headers = self._get_headers(ws)
            col = self._find_col(headers, col_name)
            if col >= 0:
                ws.update_cell(row_idx, col + 1, value)
            else:
                # 컬럼이 없으면 추가
                new_col = len(headers) + 1
                ws.update_cell(1, new_col, col_name)
                ws.update_cell(row_idx, new_col, value)
        except Exception as e:
            logger.error(f"캠페인 셀 업데이트 에러: {e}")

    def ensure_campaign_column(self, col_name: str):
        """캠페인관리 시트에 컬럼이 없으면 추가"""
        try:
            ws = self.spreadsheet.worksheet("캠페인관리")
            headers = self._get_headers(ws)
            if col_name not in headers:
                new_col = len(headers) + 1
                ws.update_cell(1, new_col, col_name)
                logger.info(f"캠페인관리 시트에 '{col_name}' 컬럼 추가됨")
        except Exception as e:
            logger.error(f"컬럼 추가 에러: {e}")

    def get_all_reviewers(self) -> list[dict]:
        """전체 리뷰어 목록"""
        ws = self._get_ws()
        headers = self._get_headers(ws)
        all_rows = ws.get_all_values()
        results = []
        for i, row in enumerate(all_rows[1:], start=2):
            row_dict = {headers[j]: row[j] for j in range(len(headers)) if j < len(row)}
            row_dict["_row_idx"] = i
            results.append(row_dict)
        return results

    def get_today_stats(self) -> dict:
        """오늘 현황 통계"""
        today = today_str()
        all_items = self.get_all_reviewers()
        new_today = 0
        purchase_today = 0
        review_today = 0

        for item in all_items:
            if item.get("구매일", "") == today:
                purchase_today += 1
            if item.get("리뷰제출일", "") == today:
                review_today += 1
            # 양식접수일이 오늘이면 신규
            if item.get("상태", "") in (STATUS_GUIDE_SENT, STATUS_FORM_RECEIVED):
                new_today += 1

        return {
            "new_today": new_today,
            "purchase_today": purchase_today,
            "review_today": review_today,
            "total": len(all_items),
        }

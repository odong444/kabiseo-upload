"""
google_client.py - Google Sheets + Drive client for upload_server

base64 env var에서 credentials 복원하여 Google API 접근.
기존 drive_uploader.py / sheets_manager.py 패턴 재사용.

캡쳐 업로드 + 홍보 캠페인 API 지원.
"""

import os
import io
import json
import base64
import tempfile
import logging

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

logger = logging.getLogger(__name__)

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

# 환경변수에서 credentials JSON 복원 (base64 인코딩)
_creds_b64 = os.environ.get("GOOGLE_CREDENTIALS_B64", "")
_spreadsheet_id = os.environ.get("SPREADSHEET_ID", "")
_drive_folder_order = os.environ.get("DRIVE_FOLDER_ORDER", "")
_drive_folder_review = os.environ.get("DRIVE_FOLDER_REVIEW", "")
_promotion_api_key = os.environ.get("PROMOTION_API_KEY", "")


def get_promotion_api_key() -> str:
    return _promotion_api_key


def _safe_int(value, default=0) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _get_credentials():
    """base64 env var에서 ServiceAccountCredentials 생성"""
    if not _creds_b64:
        raise RuntimeError("GOOGLE_CREDENTIALS_B64 환경변수가 설정되지 않았습니다.")
    creds_json = json.loads(base64.b64decode(_creds_b64))
    return ServiceAccountCredentials.from_json_keyfile_dict(creds_json, SCOPES)


def _get_gspread_client():
    creds = _get_credentials()
    return gspread.authorize(creds)


def _get_drive_service():
    creds = _get_credentials()
    return build("drive", "v3", credentials=creds)


# ────────────────────── Sheets 함수 ──────────────────────

def search_by_depositor(capture_type: str, name: str) -> list[dict]:
    """예금주명으로 시트 검색.

    capture_type: "purchase" → 상태=양식접수 목록
                  "review"   → 상태=리뷰대기 목록
    Returns: [{"row_idx": int, "제품명": str, "아이디": str, "구매일": str, ...}, ...]
    """
    client = _get_gspread_client()
    spreadsheet = client.open_by_key(_spreadsheet_id)
    ws = spreadsheet.worksheet("카비서_정리")
    headers = ws.row_values(1)
    all_values = ws.get_all_values()

    depositor_col = headers.index("예금주") if "예금주" in headers else -1
    status_col = headers.index("상태") if "상태" in headers else -1

    if depositor_col < 0 or status_col < 0:
        return []

    target_status = "양식접수" if capture_type == "purchase" else "리뷰대기"

    results = []
    for i, row in enumerate(all_values[1:], start=2):
        if len(row) <= max(depositor_col, status_col):
            continue
        if row[depositor_col] == name and row[status_col] == target_status:
            row_dict = {headers[j]: row[j] for j in range(len(headers)) if j < len(row)}
            row_dict["_row_idx"] = i
            results.append(row_dict)

    return results


def update_sheet_after_upload(capture_type: str, row_idx: int, drive_link: str):
    """업로드 완료 후 시트 업데이트.

    purchase: 구매캡쳐링크 기록, 상태→리뷰대기
    review:   리뷰캡쳐링크 기록, 상태→리뷰완료, 리뷰제출일 기록
    """
    from datetime import datetime, timezone, timedelta
    kst = timezone(timedelta(hours=9))
    today = datetime.now(kst).strftime("%Y-%m-%d")

    client = _get_gspread_client()
    spreadsheet = client.open_by_key(_spreadsheet_id)
    ws = spreadsheet.worksheet("카비서_정리")
    headers = ws.row_values(1)

    def _update_cell(col_name, value):
        if col_name in headers:
            col_idx = headers.index(col_name) + 1
            ws.update_cell(row_idx, col_idx, value)

    if capture_type == "purchase":
        _update_cell("구매캡쳐링크", drive_link)
        _update_cell("상태", "리뷰대기")
    elif capture_type == "review":
        _update_cell("리뷰캡쳐링크", drive_link)
        _update_cell("상태", "리뷰완료")
        _update_cell("리뷰제출일", today)


# ────────────────────── Drive 함수 ──────────────────────

def upload_to_drive(file_storage, capture_type: str = "purchase", description: str = "") -> str:
    """Flask FileStorage → Google Drive 업로드 → 공유링크 반환

    capture_type: "purchase" → order 폴더, "review" → review 폴더
    """
    service = _get_drive_service()
    folder_id = _drive_folder_order if capture_type == "purchase" else _drive_folder_review

    filename = file_storage.filename or "upload.jpg"
    content_type = file_storage.content_type or "image/jpeg"

    file_bytes = file_storage.read()
    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=content_type, resumable=True)

    metadata = {
        "name": filename,
        "description": description,
    }
    if folder_id:
        metadata["parents"] = [folder_id]

    uploaded = service.files().create(
        body=metadata, media_body=media, fields="id, webViewLink"
    ).execute()

    file_id = uploaded["id"]

    # 공유 설정 (누구나 볼 수 있게)
    service.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()

    link = uploaded.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")
    return link


# ────────────────────── 캠페인 홍보 API 함수 ──────────────────────

def get_campaigns_need_recruit() -> list[dict]:
    """홍보 필요한 캠페인 목록 조회 (상태=모집중, 달성률 < 100%)

    캠페인관리 탭에서 모집중 캠페인을 가져오고,
    카비서_정리 탭에서 엔트리 수를 집계하여 달성률/남은수량을 계산.
    """
    client = _get_gspread_client()
    spreadsheet = client.open_by_key(_spreadsheet_id)

    # 캠페인관리 탭 조회
    ws_campaign = spreadsheet.worksheet("캠페인관리")
    headers_c = ws_campaign.row_values(1)
    all_campaigns = ws_campaign.get_all_values()

    # 카비서_정리 탭 조회 (엔트리 집계용)
    ws_entry = spreadsheet.worksheet("카비서_정리")
    headers_e = ws_entry.row_values(1)
    all_entries = ws_entry.get_all_values()

    def _col(headers, name):
        return headers.index(name) if name in headers else -1

    def _val(row, idx):
        return row[idx] if idx >= 0 and len(row) > idx else ""

    # 캠페인관리 컬럼 인덱스
    ci = {name: _col(headers_c, name) for name in [
        "캠페인ID", "상태", "상품명", "업체명", "옵션", "총수량",
        "유입방식", "키워드", "리뷰제공", "주말작업", "모집글", "마지막홍보",
    ]}

    # 카비서_정리 컬럼 인덱스
    ei_cid = _col(headers_e, "캠페인ID")
    ei_status = _col(headers_e, "상태")

    # 캠페인별 활성 엔트리 수 집계
    active_statuses = {"신청", "가이드전달", "양식접수", "리뷰대기", "리뷰완료", "정산완료"}
    entry_counts = {}
    for row in all_entries[1:]:
        if ei_cid < 0 or ei_status < 0:
            break
        if len(row) > max(ei_cid, ei_status):
            cid = row[ei_cid]
            st = row[ei_status]
            if st in active_statuses:
                entry_counts[cid] = entry_counts.get(cid, 0) + 1

    results = []
    for row in all_campaigns[1:]:
        status = _val(row, ci["상태"])
        if status != "모집중":
            continue

        campaign_id = _val(row, ci["캠페인ID"])
        total = _safe_int(_val(row, ci["총수량"]))
        if total <= 0:
            continue

        current = entry_counts.get(campaign_id, 0)
        achievement = round(current / total * 100, 1)
        remaining = max(0, total - current)

        if achievement >= 100:
            continue

        product = _val(row, ci["상품명"])
        store = _val(row, ci["업체명"])
        option = _val(row, ci["옵션"])
        method = _val(row, ci["유입방식"])
        kw = _val(row, ci["키워드"])
        review_yn = _val(row, ci["리뷰제공"])
        weekend_yn = _val(row, ci["주말작업"])
        custom_post = _val(row, ci["모집글"]).strip()
        last_promo = _val(row, ci["마지막홍보"])

        post_text = custom_post if custom_post else _generate_recruitment_post(
            product, store, option, remaining, method, kw, review_yn, weekend_yn
        )

        results.append({
            "id": campaign_id,
            "상품명": product,
            "스토어명": store,
            "옵션": option,
            "남은수량": remaining,
            "유입방식": method,
            "키워드": kw,
            "리뷰제공": review_yn,
            "주말작업": weekend_yn,
            "모집글": post_text,
            "우선순위": 0,
            "달성률": achievement,
            "마지막홍보": last_promo,
        })

    # 달성률 낮은 순 정렬
    results.sort(key=lambda x: x["달성률"])
    for i, r in enumerate(results):
        r["우선순위"] = i + 1

    logger.info("홍보 필요 캠페인 %d개 조회", len(results))
    return results


def _generate_recruitment_post(product, store, option, remaining,
                                method, keyword, review_yn, weekend_yn) -> str:
    """캠페인 데이터에서 모집글 자동 생성"""
    lines = [f"[체험단 모집] {product}", ""]
    if store:
        lines.append(f"스토어: {store}")
    if option:
        lines.append(f"옵션: {option}")
    lines.append(f"남은수량: {remaining}명")
    if method:
        lines.append(f"유입방식: {method}")
    if keyword:
        lines.append(f"키워드: {keyword}")
    if review_yn:
        lines.append(f"리뷰제공: {review_yn}")
    if weekend_yn:
        lines.append(f"주말작업: {weekend_yn}")
    lines.append("")
    lines.append("참여를 원하시면 채팅방에서 '신청'이라고 입력해주세요!")
    return "\n".join(lines)


def record_promotion(campaign_id: str, chatroom: str, posted_at: str):
    """홍보 완료 기록 — 캠페인관리 탭의 마지막홍보 컬럼 업데이트"""
    client = _get_gspread_client()
    spreadsheet = client.open_by_key(_spreadsheet_id)
    ws = spreadsheet.worksheet("캠페인관리")
    headers = ws.row_values(1)
    all_values = ws.get_all_values()

    c_cid = headers.index("캠페인ID") if "캠페인ID" in headers else -1
    if c_cid < 0:
        logger.warning("캠페인ID 컬럼을 찾을 수 없음")
        return

    if "마지막홍보" not in headers:
        logger.warning("마지막홍보 컬럼이 캠페인관리 탭에 없음")
        return

    c_last = headers.index("마지막홍보") + 1  # 1-based

    for i, row in enumerate(all_values[1:], start=2):
        if len(row) > c_cid and row[c_cid] == campaign_id:
            ws.update_cell(i, c_last, posted_at)
            logger.info("홍보 기록: 캠페인=%s, 채팅방=%s, 시간=%s",
                        campaign_id, chatroom, posted_at)
            return

    logger.warning("캠페인 미발견: %s", campaign_id)


def get_campaign_status(campaign_id: str) -> dict | None:
    """캠페인 달성률 조회"""
    client = _get_gspread_client()
    spreadsheet = client.open_by_key(_spreadsheet_id)

    # 캠페인관리에서 기본 정보
    ws_c = spreadsheet.worksheet("캠페인관리")
    headers_c = ws_c.row_values(1)
    all_campaigns = ws_c.get_all_values()

    c_cid = headers_c.index("캠페인ID") if "캠페인ID" in headers_c else -1
    c_status = headers_c.index("상태") if "상태" in headers_c else -1
    c_total = headers_c.index("총수량") if "총수량" in headers_c else -1

    campaign_row = None
    for row in all_campaigns[1:]:
        if c_cid >= 0 and len(row) > c_cid and row[c_cid] == campaign_id:
            campaign_row = row
            break

    if not campaign_row:
        return None

    total = _safe_int(campaign_row[c_total]) if c_total >= 0 and len(campaign_row) > c_total else 0
    status = campaign_row[c_status] if c_status >= 0 and len(campaign_row) > c_status else ""

    # 카비서_정리에서 엔트리 수 집계
    ws_e = spreadsheet.worksheet("카비서_정리")
    headers_e = ws_e.row_values(1)
    all_entries = ws_e.get_all_values()

    e_cid = headers_e.index("캠페인ID") if "캠페인ID" in headers_e else -1
    e_status = headers_e.index("상태") if "상태" in headers_e else -1

    active_statuses = {"신청", "가이드전달", "양식접수", "리뷰대기", "리뷰완료", "정산완료"}
    count = 0
    for row in all_entries[1:]:
        if e_cid >= 0 and e_status >= 0 and len(row) > max(e_cid, e_status):
            if row[e_cid] == campaign_id and row[e_status] in active_statuses:
                count += 1

    achievement = round(count / total * 100, 1) if total > 0 else 0
    remaining = max(0, total - count)

    return {
        "달성률": achievement,
        "상태": status,
        "남은수량": remaining,
    }

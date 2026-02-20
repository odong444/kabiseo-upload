"""
google_client.py - Google Sheets + Drive client for upload_server

base64 env var에서 credentials 복원하여 Google API 접근.
기존 drive_uploader.py / sheets_manager.py 패턴 재사용.
"""

import os
import io
import json
import base64
import tempfile

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

# 환경변수에서 credentials JSON 복원 (base64 인코딩)
_creds_b64 = os.environ.get("GOOGLE_CREDENTIALS_B64", "")
_spreadsheet_id = os.environ.get("SPREADSHEET_ID", "")
_drive_folder_order = os.environ.get("DRIVE_FOLDER_ORDER", "")
_drive_folder_review = os.environ.get("DRIVE_FOLDER_REVIEW", "")


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

"""
google_client.py - Google Sheets + Drive 클라이언트 (확장)

기존 upload_server 코드 + modules 통합 인터페이스.
"""

import os
import json
import base64
import logging

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build

from modules.sheets_manager import SheetsManager
from modules.drive_uploader import DriveUploader

logger = logging.getLogger(__name__)

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

# 환경변수
_creds_b64 = os.environ.get("GOOGLE_CREDENTIALS_B64", "")
_spreadsheet_id = os.environ.get("SPREADSHEET_ID", "")
_drive_folder_order = os.environ.get("DRIVE_FOLDER_ORDER", "")
_drive_folder_review = os.environ.get("DRIVE_FOLDER_REVIEW", "")


def _get_credentials():
    if not _creds_b64:
        raise RuntimeError("GOOGLE_CREDENTIALS_B64 환경변수가 설정되지 않았습니다.")
    creds_json = json.loads(base64.b64decode(_creds_b64))
    return ServiceAccountCredentials.from_json_keyfile_dict(creds_json, SCOPES)


def get_gspread_client():
    creds = _get_credentials()
    return gspread.authorize(creds)


def get_drive_service():
    creds = _get_credentials()
    return build("drive", "v3", credentials=creds)


# ──────── 싱글톤 매니저 인스턴스 ────────

_sheets_manager = None
_drive_uploader = None


def get_sheets_manager() -> SheetsManager:
    global _sheets_manager
    if _sheets_manager is None:
        client = get_gspread_client()
        _sheets_manager = SheetsManager(client, _spreadsheet_id)
    return _sheets_manager


def get_drive_uploader() -> DriveUploader:
    global _drive_uploader
    if _drive_uploader is None:
        service = get_drive_service()
        _drive_uploader = DriveUploader(service, _drive_folder_order, _drive_folder_review)
    return _drive_uploader


# ──────── 기존 호환 함수 ────────

def search_by_depositor(capture_type: str, name: str) -> list[dict]:
    return get_sheets_manager().search_by_depositor(capture_type, name)


def upload_to_drive(file_storage, capture_type: str = "purchase", description: str = "") -> str:
    return get_drive_uploader().upload_from_flask_file(file_storage, capture_type, description)


def update_sheet_after_upload(capture_type: str, row_idx: int, drive_link: str):
    return get_sheets_manager().update_after_upload(capture_type, row_idx, drive_link)

"""
google_client.py - Google Sheets + Drive 클라이언트 (확장)

- Sheets: Service Account (기존)
- Drive: OAuth 2.0 (사용자 계정, 스토리지 용량 사용)
"""

import os
import json
import base64
import logging

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from modules.sheets_manager import SheetsManager
from modules.drive_uploader import DriveUploader

logger = logging.getLogger(__name__)

SCOPES_SHEETS = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

SCOPES_DRIVE = [
    "https://www.googleapis.com/auth/drive",
]

# 환경변수
_creds_b64 = os.environ.get("GOOGLE_CREDENTIALS_B64", "")
_spreadsheet_id = os.environ.get("SPREADSHEET_ID", "")
_drive_folder_order = os.environ.get("DRIVE_FOLDER_ORDER", "")
_drive_folder_review = os.environ.get("DRIVE_FOLDER_REVIEW", "")

# OAuth 환경변수
_oauth_client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
_oauth_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
_oauth_redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI", "")
_oauth_tokens_json = os.environ.get("GOOGLE_OAUTH_TOKENS", "")


# ──────── Service Account (Sheets용) ────────

def _get_sa_credentials():
    if not _creds_b64:
        raise RuntimeError("GOOGLE_CREDENTIALS_B64 환경변수가 설정되지 않았습니다.")
    creds_json = json.loads(base64.b64decode(_creds_b64))
    return ServiceAccountCredentials.from_json_keyfile_dict(creds_json, SCOPES_SHEETS)


def get_gspread_client():
    creds = _get_sa_credentials()
    return gspread.authorize(creds)


# ──────── OAuth 2.0 (Drive용) ────────

_oauth_tokens = {}


def _load_oauth_tokens():
    """환경변수 또는 파일에서 OAuth 토큰 로드"""
    global _oauth_tokens
    if _oauth_tokens_json:
        try:
            _oauth_tokens = json.loads(_oauth_tokens_json)
            logger.info("OAuth 토큰 로드됨 (환경변수)")
            return
        except Exception as e:
            logger.warning(f"OAuth 토큰 파싱 실패: {e}")

    # 파일 fallback
    try:
        with open("tokens.json", "r") as f:
            _oauth_tokens = json.load(f)
            logger.info("OAuth 토큰 로드됨 (파일)")
    except FileNotFoundError:
        logger.warning("OAuth 토큰 없음 - /auth로 인증 필요")


def save_oauth_tokens(tokens: dict):
    """OAuth 토큰 저장 (파일)"""
    global _oauth_tokens
    _oauth_tokens = tokens
    try:
        with open("tokens.json", "w") as f:
            json.dump(tokens, f, indent=2)
        logger.info("OAuth 토큰 저장됨")
    except Exception as e:
        logger.error(f"토큰 저장 실패: {e}")


def get_oauth_credentials() -> Credentials | None:
    """OAuth Credentials 반환 (자동 리프레시)"""
    if not _oauth_tokens or "access_token" not in _oauth_tokens:
        return None

    creds = Credentials(
        token=_oauth_tokens.get("access_token"),
        refresh_token=_oauth_tokens.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=_oauth_client_id,
        client_secret=_oauth_client_secret,
        scopes=SCOPES_DRIVE,
    )

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_oauth_tokens({
                "access_token": creds.token,
                "refresh_token": creds.refresh_token,
            })
            logger.info("OAuth 토큰 리프레시 완료")
        except Exception as e:
            logger.error(f"OAuth 토큰 리프레시 실패: {e}")
            return None

    return creds


def get_drive_service_oauth():
    """OAuth 기반 Drive 서비스 (사용자 계정 스토리지 사용)"""
    creds = get_oauth_credentials()
    if not creds:
        raise RuntimeError("OAuth 인증이 필요합니다. /auth 에서 Google 로그인을 해주세요.")
    return build("drive", "v3", credentials=creds)


def get_oauth_auth_url() -> str:
    """Google OAuth 인증 URL 생성"""
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": _oauth_client_id,
                "client_secret": _oauth_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [_oauth_redirect_uri],
            }
        },
        scopes=SCOPES_DRIVE,
    )
    flow.redirect_uri = _oauth_redirect_uri
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )
    return auth_url


def handle_oauth_callback(auth_code: str) -> dict:
    """OAuth 콜백 처리 → 토큰 저장"""
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": _oauth_client_id,
                "client_secret": _oauth_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [_oauth_redirect_uri],
            }
        },
        scopes=SCOPES_DRIVE,
    )
    flow.redirect_uri = _oauth_redirect_uri
    flow.fetch_token(code=auth_code)

    creds = flow.credentials
    tokens = {
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
    }
    save_oauth_tokens(tokens)
    return tokens


# 시작시 토큰 로드
_load_oauth_tokens()


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
    """Drive 업로드 - OAuth 우선, 실패시 Service Account fallback"""
    global _drive_uploader
    if _drive_uploader is None:
        try:
            service = get_drive_service_oauth()
            logger.info("Drive 업로더: OAuth 모드")
        except Exception as e:
            logger.warning(f"OAuth Drive 실패 ({e}), Service Account fallback")
            creds = _get_sa_credentials()
            service = build("drive", "v3", credentials=creds)
        _drive_uploader = DriveUploader(service, _drive_folder_order, _drive_folder_review)
    return _drive_uploader


def reset_drive_uploader():
    """OAuth 인증 후 Drive 업로더 재생성"""
    global _drive_uploader
    _drive_uploader = None


# ──────── 기존 호환 함수 ────────

def search_by_depositor(capture_type: str, name: str) -> list[dict]:
    return get_sheets_manager().search_by_depositor(capture_type, name)


def upload_to_drive(file_storage, capture_type: str = "purchase", description: str = "") -> str:
    return get_drive_uploader().upload_from_flask_file(file_storage, capture_type, description)


def update_sheet_after_upload(capture_type: str, row_idx: int, drive_link: str):
    return get_sheets_manager().update_after_upload(capture_type, row_idx, drive_link)

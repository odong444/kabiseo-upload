"""
models.py - 데이터 모델 / 앱 전역 인스턴스

Flask 앱에서 공유하는 매니저 인스턴스들.
"""

import os
import logging

from modules.state_store import StateStore
from modules.chat_logger import ChatLogger
from modules.campaign_manager import CampaignManager
from modules.reviewer_manager import ReviewerManager
from modules.reviewer_grader import ReviewerGrader
from modules.timeout_manager import TimeoutManager
from modules.step_machine import StepMachine
from modules.activity_logger import ActivityLogger

# ──────── 인메모리 저장소 ────────
state_store = StateStore()
chat_logger = ChatLogger()
activity_logger = ActivityLogger()

# ──────── 매니저 (init_app에서 초기화) ────────
db_manager = None
drive_uploader = None
campaign_manager = None
reviewer_manager = None
reviewer_grader = None
timeout_manager = None
step_machine = None
ai_handler = None
kakao_notifier = None
sheet_sync = None

# 하위 호환 (기존 코드에서 sheets_manager 참조하는 곳 대비)
sheets_manager = None


def init_app(web_url: str = "", socketio=None):
    """앱 시작 시 매니저 초기화"""
    global db_manager, drive_uploader, campaign_manager, sheets_manager
    global reviewer_manager, reviewer_grader, timeout_manager, step_machine, ai_handler, kakao_notifier
    global sheet_sync

    # ── PostgreSQL 초기화 ──
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url:
        from modules.db_manager import DBManager
        try:
            db_manager = DBManager(database_url)
            sheets_manager = db_manager  # 하위 호환
            logging.info("PostgreSQL 초기화 완료")
        except Exception as e:
            logging.error(f"PostgreSQL 초기화 실패: {e}")
            db_manager = None
            sheets_manager = None
    else:
        logging.warning("DATABASE_URL 미설정 - DB 비활성화")
        db_manager = None
        sheets_manager = None

    # ── Google Drive 업로더 (이미지 업로드용 유지) ──
    try:
        from google_client import get_drive_uploader
        drive_uploader = get_drive_uploader()
    except Exception as e:
        logging.warning(f"Google Drive 초기화 실패: {e}")
        drive_uploader = None

    if db_manager:
        campaign_manager = CampaignManager(db_manager)
        reviewer_manager = ReviewerManager(db_manager)
        reviewer_grader = ReviewerGrader(db_manager)
        # chat_logger, activity_logger는 메모리 기반으로 동작
        # (구글시트 제거로 영구 보관 없음 — 향후 DB 로깅 추가 가능)
    else:
        campaign_manager = None
        reviewer_manager = None
        reviewer_grader = None

    # AI 핸들러 (서버PC 릴레이)
    relay_url = os.environ.get("AI_RELAY_URL", "")
    if relay_url:
        from modules.ai_handler import AIHandler
        ai_handler = AIHandler(relay_url, api_key=os.environ.get("API_KEY", ""))
        logging.info(f"AI 핸들러 초기화 완료 (릴레이: {relay_url})")
    else:
        ai_handler = None
        logging.info("AI_RELAY_URL 미설정 - AI 응답 비활성화")

    step_machine = StepMachine(
        state_store=state_store,
        campaign_mgr=campaign_manager,
        reviewer_mgr=reviewer_manager,
        chat_logger=chat_logger,
        web_url=web_url,
        ai_handler=ai_handler,
    ) if campaign_manager and reviewer_manager else None

    # 카카오톡 알림
    if db_manager:
        from modules.kakao_notifier import KakaoNotifier
        kakao_notifier = KakaoNotifier(db_manager, web_url=web_url)
        logging.info("카카오톡 알림 초기화 완료")
    else:
        kakao_notifier = None

    # 타임아웃 매니저 (20분 취소)
    timeout_manager = TimeoutManager(state_store)
    timeout_manager.set_sheets_manager(db_manager)
    timeout_manager.set_chat_logger(chat_logger)
    if kakao_notifier:
        timeout_manager.set_kakao_notifier(kakao_notifier)
    if socketio:
        timeout_manager.set_socketio(socketio)
    timeout_manager.start()

    # ── 시트 동기화 (DB → Google Sheets) ──
    if db_manager and os.environ.get("SHEET_SYNC_ENABLED", "").lower() == "true":
        try:
            from google_client import get_gspread_client
            from modules.sheet_sync import SheetSync
            gc = get_gspread_client()
            sheet_sync = SheetSync(db_manager, gc)
            sheet_sync.start_background_sync(interval=60)
            logging.info("시트 동기화 시작 (URL: %s)", sheet_sync.spreadsheet_url)
        except Exception as e:
            logging.error("시트 동기화 초기화 실패: %s", e)
            sheet_sync = None
    else:
        sheet_sync = None

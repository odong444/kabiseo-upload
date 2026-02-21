"""
models.py - 데이터 모델 / 앱 전역 인스턴스

Flask 앱에서 공유하는 매니저 인스턴스들.
"""

from modules.state_store import StateStore
from modules.chat_logger import ChatLogger
from modules.campaign_manager import CampaignManager
from modules.reviewer_manager import ReviewerManager
from modules.reviewer_grader import ReviewerGrader
from modules.timeout_manager import TimeoutManager
from modules.step_machine import StepMachine

# ──────── 인메모리 저장소 ────────
state_store = StateStore()
chat_logger = ChatLogger()

# ──────── 매니저 (sheets 의존, init_app에서 초기화) ────────
sheets_manager = None
drive_uploader = None
campaign_manager = None
reviewer_manager = None
reviewer_grader = None
timeout_manager = None
step_machine = None


def init_app(web_url: str = "", socketio=None):
    """앱 시작 시 매니저 초기화"""
    global sheets_manager, drive_uploader, campaign_manager
    global reviewer_manager, reviewer_grader, timeout_manager, step_machine

    from google_client import get_sheets_manager, get_drive_uploader

    try:
        sheets_manager = get_sheets_manager()
        drive_uploader = get_drive_uploader()
    except Exception as e:
        import logging
        logging.warning(f"Google API 초기화 실패 (환경변수 확인 필요): {e}")
        sheets_manager = None
        drive_uploader = None

    if sheets_manager:
        # 필수 컬럼 보장
        sheets_manager.ensure_main_column("진행자이름")
        sheets_manager.ensure_campaign_column("결제금액")
        sheets_manager.ensure_campaign_column("리뷰가이드")
        sheets_manager.ensure_campaign_column("중복허용")
        sheets_manager.ensure_campaign_column("리뷰비")

        campaign_manager = CampaignManager(sheets_manager)
        reviewer_manager = ReviewerManager(sheets_manager)
        reviewer_grader = ReviewerGrader(sheets_manager)
        chat_logger.set_sheets_manager(sheets_manager)
    else:
        campaign_manager = None
        reviewer_manager = None
        reviewer_grader = None

    step_machine = StepMachine(
        state_store=state_store,
        campaign_mgr=campaign_manager,
        reviewer_mgr=reviewer_manager,
        chat_logger=chat_logger,
        web_url=web_url,
    ) if campaign_manager and reviewer_manager else None

    # 타임아웃 매니저 (20분 취소)
    timeout_manager = TimeoutManager(state_store)
    timeout_manager.set_sheets_manager(sheets_manager)
    timeout_manager.set_chat_logger(chat_logger)
    if socketio:
        timeout_manager.set_socketio(socketio)
    timeout_manager.start()

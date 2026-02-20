"""
app.py - Flask 메인 + SocketIO

카비서 웹 통합 서버:
- 리뷰어 웹 채팅
- 사진 업로드
- 진행현황 / 입금현황
- 관리자 대시보드
- 서버PC 카카오톡 연동 API
"""

import os
import logging

from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_socketio import SocketIO

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "kabiseo-web-secret-key")

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# ──────── 블루프린트 등록 ────────
from api import api_bp
from admin import admin_bp

app.register_blueprint(api_bp)
app.register_blueprint(admin_bp)

# ──────── WebSocket 핸들러 등록 ────────
import chat_handler
chat_handler.register_handlers(socketio)

# ──────── 앱 초기화 ────────
WEB_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
if WEB_URL and not WEB_URL.startswith("http"):
    WEB_URL = f"https://{WEB_URL}"

import models
models.init_app(web_url=WEB_URL)


# ════════════════════════════════════════
# 리뷰어 페이지 라우트
# ════════════════════════════════════════

@app.route("/")
def index():
    """메인: 로그인(이름+연락처) 또는 채팅 리다이렉트"""
    return render_template("login.html")


@app.route("/chat")
def chat():
    """채팅 화면"""
    return render_template("chat.html")


@app.route("/status")
def status():
    """내 진행현황"""
    return render_template("status.html")


@app.route("/payment")
def payment():
    """입금현황"""
    return render_template("payment.html")


# ──────── 사진 업로드 (기존 통합) ────────

@app.route("/upload")
def upload():
    """사진 제출 메인"""
    return render_template("upload.html")


@app.route("/upload/purchase")
def upload_purchase():
    """구매캡쳐 검색"""
    return render_template("upload_search.html", capture_type="purchase", title="구매 캡쳐 제출")


@app.route("/upload/review")
def upload_review():
    """리뷰캡쳐 검색"""
    return render_template("upload_search.html", capture_type="review", title="리뷰 캡쳐 제출")


@app.route("/upload/purchase/search")
def upload_purchase_search():
    q = request.args.get("q", "").strip()
    phone = request.args.get("phone", "").strip()
    if not q:
        flash("검색어를 입력해주세요.")
        return redirect(url_for("upload_purchase"))

    if models.sheets_manager:
        items = models.sheets_manager.search_by_name_phone_or_depositor("purchase", q, phone)
    else:
        items = []

    return render_template(
        "upload_items.html", capture_type="purchase", title="구매 캡쳐 제출",
        query=q, items=items,
    )


@app.route("/upload/review/search")
def upload_review_search():
    q = request.args.get("q", "").strip()
    phone = request.args.get("phone", "").strip()
    if not q:
        flash("검색어를 입력해주세요.")
        return redirect(url_for("upload_review"))

    if models.sheets_manager:
        items = models.sheets_manager.search_by_name_phone_or_depositor("review", q, phone)
    else:
        items = []

    return render_template(
        "upload_items.html", capture_type="review", title="리뷰 캡쳐 제출",
        query=q, items=items,
    )


@app.route("/upload/purchase/<int:row>", methods=["POST"])
def upload_purchase_submit(row):
    return _handle_upload("purchase", row)


@app.route("/upload/review/<int:row>", methods=["POST"])
def upload_review_submit(row):
    return _handle_upload("review", row)


def _handle_upload(capture_type: str, row: int):
    """공통 업로드 처리"""
    file = request.files.get("capture")
    if not file or file.filename == "":
        flash("파일을 선택해주세요.")
        return redirect(request.referrer or url_for("upload"))

    if not models.drive_uploader or not models.sheets_manager:
        flash("시스템 초기화 중입니다. 잠시 후 다시 시도해주세요.")
        return redirect(request.referrer or url_for("upload"))

    try:
        desc = f"{capture_type}_row{row}"
        drive_link = models.drive_uploader.upload_from_flask_file(file, capture_type, desc)
        models.sheets_manager.update_after_upload(capture_type, row, drive_link)

        label = "구매" if capture_type == "purchase" else "리뷰"
        return render_template(
            "upload_success.html",
            title=f"{label} 캡쳐 제출 완료",
            message=f"{label} 캡쳐가 성공적으로 제출되었습니다!",
            capture_type=capture_type,
        )
    except Exception as e:
        logger.error(f"업로드 에러: {e}", exc_info=True)
        flash(f"업로드 중 오류가 발생했습니다: {e}")
        return redirect(request.referrer or url_for("upload"))


# ──────── API: 진행현황 / 입금현황 (AJAX) ────────

@app.route("/api/status")
def api_status():
    """리뷰어 진행현황 JSON"""
    name = request.args.get("name", "").strip()
    phone = request.args.get("phone", "").strip()
    if not name or not phone or not models.reviewer_manager:
        return {"in_progress": [], "completed": []}
    items = models.reviewer_manager.get_items(name, phone)
    return items


@app.route("/api/payment")
def api_payment():
    """입금현황 JSON"""
    name = request.args.get("name", "").strip()
    phone = request.args.get("phone", "").strip()
    if not name or not phone or not models.reviewer_manager:
        return {"paid": [], "pending": [], "no_review": []}
    payments = models.reviewer_manager.get_payments(name, phone)
    return payments


# ════════════════════════════════════════
# 실행
# ════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    socketio.run(app, host="0.0.0.0", port=port, debug=True)

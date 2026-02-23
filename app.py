"""
app.py - Flask 메인 + SocketIO

카비서 웹 통합 서버:
- 리뷰어 웹 채팅
- 사진 업로드
- 진행현황 / 입금현황
- 관리자 대시보드
- 서버PC 카카오톡 연동 API
"""

import eventlet
eventlet.monkey_patch()

import os
import logging

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
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
models.init_app(web_url=WEB_URL, socketio=socketio)


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

    if models.db_manager:
        items = models.db_manager.search_by_name_phone_or_depositor("purchase", q, phone)
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

    if models.db_manager:
        items = models.db_manager.search_by_name_phone_or_depositor("review", q, phone)
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


def _make_upload_filename(capture_type: str, row_idx: int, original_name: str) -> str:
    """업로드 파일명 생성: 업체명_수취인명_구매내역(or 리뷰).확장자"""
    import os as _os
    ext = _os.path.splitext(original_name)[1] or ".jpg"
    label = "구매내역" if capture_type == "purchase" else "리뷰"

    row_data = {}
    if models.db_manager:
        try:
            row_data = models.db_manager.get_row_dict(row_idx)
        except Exception:
            pass

    company = row_data.get("업체명", "").strip() or "업체"
    recipient = row_data.get("수취인명", "").strip() or "미지정"
    purchase_date = row_data.get("구매일", "").strip().replace("-", "").replace("/", "").replace(".", "") or "날짜없음"
    return f"{purchase_date}_{company}_{recipient}_{label}{ext}"


def _touch_reviewer_by_row(row_idx: int):
    """업로드 완료 시 해당 리뷰어의 타임아웃 타이머 리셋"""
    try:
        if not models.db_manager or not models.state_store:
            return
        row_data = models.db_manager.get_row_dict(row_idx)
        name = row_data.get("진행자이름", "") or row_data.get("수취인명", "")
        phone = row_data.get("진행자연락처", "") or row_data.get("연락처", "")
        if name and phone:
            state = models.state_store.get_by_id(f"{name}_{phone}")
            if state:
                state.touch()
                logger.info(f"업로드로 타임아웃 리셋: {name}_{phone}")
    except Exception as e:
        logger.debug(f"타임아웃 리셋 실패 (무시): {e}")


def _handle_upload(capture_type: str, row: int):
    """공통 업로드 처리"""
    file = request.files.get("capture")
    if not file or file.filename == "":
        flash("파일을 선택해주세요.")
        return redirect(request.referrer or url_for("upload"))

    if not models.drive_uploader or not models.db_manager:
        flash("시스템 초기화 중입니다. 잠시 후 다시 시도해주세요.")
        return redirect(request.referrer or url_for("upload"))

    try:
        filename = _make_upload_filename(capture_type, row, file.filename)
        desc = f"{capture_type}_row{row}"
        drive_link = models.drive_uploader.upload(
            file.read(), filename, file.content_type or "image/jpeg", capture_type, desc
        )
        models.db_manager.update_after_upload(capture_type, row, drive_link)
        _touch_reviewer_by_row(row)

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


# ──────── API: AJAX 업로드 ────────

@app.route("/api/upload", methods=["POST"])
def api_upload():
    """AJAX 단건 업로드 (일괄 제출에서 호출)"""
    file = request.files.get("capture")
    capture_type = request.form.get("capture_type", "")
    row = request.form.get("row", "0")

    if not file or file.filename == "":
        return jsonify({"ok": False, "message": "파일을 선택해주세요."}), 400

    if capture_type not in ("purchase", "review"):
        return jsonify({"ok": False, "message": "잘못된 유형입니다."}), 400

    if not models.drive_uploader or not models.db_manager:
        return jsonify({"ok": False, "message": "시스템 초기화 중입니다."}), 503

    try:
        row_idx = int(row)
        filename = _make_upload_filename(capture_type, row_idx, file.filename)
        desc = f"{capture_type}_row{row_idx}"
        drive_link = models.drive_uploader.upload(
            file.read(), filename, file.content_type or "image/jpeg", capture_type, desc
        )
        models.db_manager.update_after_upload(capture_type, row_idx, drive_link)
        _touch_reviewer_by_row(row_idx)

        label = "구매" if capture_type == "purchase" else "리뷰"
        return jsonify({"ok": True, "message": f"{label} 캡쳐 제출 완료!"})
    except Exception as e:
        logger.error(f"AJAX 업로드 에러: {e}", exc_info=True)
        return jsonify({"ok": False, "message": f"업로드 실패: {e}"}), 500


# ──────── API: 진행현황 / 입금현황 (AJAX) ────────

@app.route("/api/debug/campaigns")
def api_debug_campaigns():
    """임시 캠페인 디버그 (공개)"""
    result = {"campaigns": [], "active": [], "cards": [], "count_all": {}, "error": None}
    try:
        if models.campaign_manager:
            all_c = models.campaign_manager.get_all_campaigns()
            result["campaigns"] = [
                {k: v for k, v in c.items() if k in ("캠페인ID", "상품명", "상태", "총수량", "완료수량", "일수량", "공개여부", "구매가능시간")}
                for c in all_c
            ]
            active = models.campaign_manager.get_active_campaigns()
            result["active"] = [
                {k: v for k, v in c.items() if k in ("캠페인ID", "상품명", "상태", "총수량", "_남은수량", "_buy_time_active")}
                for c in active
            ]
            cards = models.campaign_manager.build_campaign_cards("테스트", "010-0000-0000")
            result["cards"] = cards
        else:
            result["error"] = "campaign_manager is None"
        if models.db_manager:
            try:
                result["count_all"] = models.db_manager.count_all_campaigns()
            except Exception as e:
                result["count_all_error"] = str(e)
        else:
            result["error"] = (result.get("error") or "") + " | db_manager is None"
    except Exception as e:
        result["error"] = str(e)
        import traceback
        result["traceback"] = traceback.format_exc()
    return jsonify(result)


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


# ──────── Google OAuth 인증 (Drive 업로드용) ────────

@app.route("/auth")
def google_auth():
    """Google OAuth 인증 시작"""
    import google_client
    try:
        auth_url = google_client.get_oauth_auth_url()
        return redirect(auth_url)
    except Exception as e:
        logger.error(f"OAuth 인증 URL 생성 실패: {e}")
        return f"OAuth 설정 오류: {e}", 500


@app.route("/auth/callback")
def auth_callback():
    """Google OAuth 콜백"""
    import google_client
    code = request.args.get("code")
    if not code:
        return "인증 코드가 없습니다.", 400

    try:
        tokens = google_client.handle_oauth_callback(code)
        google_client.reset_drive_uploader()
        models.drive_uploader = google_client.get_drive_uploader()
        # 토큰을 로그에 출력 (환경변수 설정용)
        import json
        tokens_json = json.dumps(tokens)
        logger.info(f"OAUTH_TOKENS_FOR_ENV={tokens_json}")
        logger.info("OAuth 인증 완료, Drive 업로더 재생성")
        return render_template("oauth_success.html", tokens_json=tokens_json)
    except Exception as e:
        logger.error(f"OAuth 콜백 에러: {e}", exc_info=True)
        return f"인증 실패: {e}", 500


# ════════════════════════════════════════
# 실행
# ════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    socketio.run(app, host="0.0.0.0", port=port, debug=True)

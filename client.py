"""
client.py - 업체 관리자 포털 라우트

업체가 캠페인을 요청하고, 진행 현황을 확인하는 포털.
"""

import os
import re
import uuid
import logging
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, flash

import models
from modules.utils import today_str, safe_int, extract_product_codes

logger = logging.getLogger(__name__)

client_bp = Blueprint("client", __name__, url_prefix="/client")


# ─── 인증 ───

def client_required(f):
    """업체 로그인 데코레이터"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("client_id"):
            return redirect(url_for("client.login"))
        return f(*args, **kwargs)
    return decorated


@client_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if session.get("client_id"):
            return redirect(url_for("client.dashboard"))
        return render_template("client/login.html")

    login_id = request.form.get("login_id", "").strip()
    password = request.form.get("password", "").strip()

    if not login_id or not password:
        flash("아이디와 비밀번호를 입력해주세요.")
        return redirect(url_for("client.login"))

    client = models.db_manager.get_client_by_login(login_id)
    if not client:
        flash("아이디 또는 비밀번호가 올바르지 않습니다.")
        return redirect(url_for("client.login"))

    if not client.get("is_active", True):
        flash("비활성화된 계정입니다. 담당자에게 문의해주세요.")
        return redirect(url_for("client.login"))

    from werkzeug.security import check_password_hash
    if not check_password_hash(client["password_hash"], password):
        flash("아이디 또는 비밀번호가 올바르지 않습니다.")
        return redirect(url_for("client.login"))

    session["client_id"] = client["id"]
    session["client_company"] = client["company_name"]
    session["client_login_id"] = client["login_id"]
    return redirect(url_for("client.dashboard"))


@client_bp.route("/logout")
def logout():
    session.pop("client_id", None)
    session.pop("client_company", None)
    session.pop("client_login_id", None)
    return redirect(url_for("client.login"))


# ─── 대시보드 ───

@client_bp.route("/dashboard")
@client_required
def dashboard():
    client_id = session["client_id"]
    campaigns = models.db_manager.get_client_campaigns(client_id)
    stats = models.db_manager.get_client_campaign_stats(client_id)

    # 각 캠페인의 구매/리뷰 진행률 일괄 계산
    campaign_ids = [c.get("캠페인ID", "") for c in campaigns if c.get("캠페인ID")]
    progress_counts = {}
    if campaign_ids:
        rows = models.db_manager._fetchall(
            """SELECT campaign_id,
                      COUNT(*) FILTER (WHERE status NOT IN ('신청','가이드전달','취소','타임아웃취소','')) as purchased,
                      COUNT(*) FILTER (WHERE status IN ('리뷰완료','입금대기','입금완료')) as reviewed
               FROM progress
               WHERE campaign_id = ANY(%s)
               GROUP BY campaign_id""",
            (campaign_ids,)
        )
        for r in rows:
            progress_counts[r["campaign_id"]] = {
                "purchased": r["purchased"],
                "reviewed": r["reviewed"],
            }

    for c in campaigns:
        cid = c.get("캠페인ID", "")
        total = safe_int(c.get("총수량", 0))
        counts = progress_counts.get(cid, {"purchased": 0, "reviewed": 0})
        c["purchase_pct"] = round(counts["purchased"] / total * 100) if total > 0 else 0
        c["review_pct"] = round(counts["reviewed"] / total * 100) if total > 0 else 0
        c["purchased"] = counts["purchased"]
        c["reviewed"] = counts["reviewed"]

    # 임시저장 목록
    drafts = models.db_manager.get_drafts("client", client_id)

    return render_template("client/dashboard.html",
                           campaigns=campaigns, stats=stats,
                           drafts=drafts,
                           company_name=session.get("client_company", ""))


# ─── 캠페인 요청 ───

@client_bp.route("/campaign/new", methods=["GET"])
@client_required
def campaign_new():
    # 임시저장 불러오기
    draft = None
    draft_id = request.args.get("draft")
    if draft_id:
        draft = models.db_manager.get_campaign_by_id(draft_id)
        if draft and (draft.get("상태") != "임시저장" or str(draft.get("업체ID", "")) != str(session["client_id"])):
            draft = None

    return render_template("client/campaign_request.html",
                           draft=draft,
                           company_name=session.get("client_company", ""))


@client_bp.route("/campaign/new", methods=["POST"])
@client_required
def campaign_new_post():
    if not models.db_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("client.dashboard"))

    draft_id = request.form.get("draft_id", "").strip()

    fields = [
        "캠페인유형", "플랫폼", "업체명", "캠페인명", "상품명",
        "총수량", "일수량", "진행일수", "1인일일제한",
        "상품금액", "리뷰비", "중복허용", "구매가능시간", "캠페인가이드",
        "상품링크", "옵션", "키워드", "유입방식", "리뷰기한일수",
    ]

    if draft_id:
        # 임시저장에서 정식 제출: update
        data = {
            "상태": "승인대기",
            "등록일": today_str(),
            "업체ID": session["client_id"],
        }
    else:
        campaign_id = str(uuid.uuid4())[:8]
        data = {
            "캠페인ID": campaign_id,
            "등록일": today_str(),
            "상태": "승인대기",
            "완료수량": "0",
            "업체ID": session["client_id"],
        }

    # Look up client's agency to set agency_id
    _client_info = models.db_manager.get_client_by_id(session["client_id"])
    if _client_info and _client_info.get("agency_id"):
        data["대행사ID"] = _client_info["agency_id"]

    for field in fields:
        data[field] = request.form.get(field, "").strip()

    # 업체명 기본값: 세션의 company_name
    if not data.get("업체명"):
        data["업체명"] = session.get("client_company", "")

    # 상품링크에서 상품코드 자동 추출
    product_link = data.get("상품링크", "")
    if product_link:
        codes = extract_product_codes(product_link)
        if codes:
            data["상품코드"] = codes

    # 시작일
    start_date = request.form.get("시작일", "").strip() or today_str()

    # 일정: 수동 입력값 우선, 없으면 자동 생성
    manual_schedule = request.form.get("일정", "").strip()
    if manual_schedule:
        data["일정"] = [safe_int(x) for x in re.split(r"[,\s]+", manual_schedule) if x.strip()]
        data["시작일"] = start_date
    else:
        total = safe_int(data.get("총수량", 0))
        daily_str = data.get("일수량", "").strip()
        days = safe_int(data.get("진행일수", 0))
        if total > 0 and days > 0 and daily_str:
            range_match = re.match(r"(\d+)\s*[-~]\s*(\d+)", daily_str)
            if range_match:
                lo, hi = int(range_match.group(1)), int(range_match.group(2))
            else:
                lo = hi = safe_int(daily_str)
            if lo > 0 and hi >= lo:
                from admin import _generate_schedule
                schedule = _generate_schedule(total, lo, hi, days)
                data["일정"] = schedule
                data["시작일"] = start_date

    # 상품이미지 파일 업로드
    image_file = request.files.get("상품이미지")
    if image_file and image_file.filename:
        try:
            if models.drive_uploader:
                link = models.drive_uploader.upload_from_flask_file(
                    image_file, capture_type="purchase",
                    description=f"캠페인 상품이미지: {data.get('상품명', '')}"
                )
                data["상품이미지"] = link
        except Exception as e:
            logger.error(f"상품이미지 업로드 에러: {e}")

    try:
        if draft_id:
            models.db_manager.update_campaign(draft_id, data)
            display_name = data.get("캠페인명", "").strip() or data.get("상품명", "")
        else:
            models.db_manager.create_campaign(data)
            display_name = data.get("캠페인명", "").strip() or data["상품명"]
        flash(f"캠페인 '{display_name}' 요청이 접수되었습니다. 담당자 승인 후 진행됩니다.")
    except Exception as e:
        logger.error(f"캠페인 요청 에러: {e}")
        flash(f"요청 중 오류가 발생했습니다: {e}")

    return redirect(url_for("client.dashboard"))


# ─── 캠페인 임시저장 ───

@client_bp.route("/campaign/draft", methods=["POST"])
@client_required
def campaign_draft_save():
    """업체 캠페인 임시저장"""
    if not models.db_manager:
        return jsonify({"ok": False, "error": "시스템 초기화 중"})

    client_id = session["client_id"]
    draft_id = request.form.get("draft_id", "").strip()

    fields = [
        "캠페인유형", "플랫폼", "업체명", "캠페인명", "상품명",
        "총수량", "일수량", "진행일수", "1인일일제한",
        "상품금액", "리뷰비", "중복허용", "구매가능시간", "캠페인가이드",
        "상품링크", "옵션", "키워드", "유입방식", "리뷰기한일수",
    ]

    data = {}
    for f in fields:
        val = request.form.get(f, "")
        if val:
            data[f] = val

    data["상태"] = "임시저장"
    data["업체ID"] = client_id

    # 대행사 자동 연결
    _client_info = models.db_manager.get_client_by_id(client_id)
    if _client_info and _client_info.get("agency_id"):
        data["대행사ID"] = _client_info["agency_id"]

    # 시작일/일정
    start_date = request.form.get("시작일", "").strip()
    if start_date:
        data["시작일"] = start_date
    manual_schedule = request.form.get("일정", "").strip()
    if manual_schedule:
        data["일정"] = [safe_int(x) for x in re.split(r"[,\s]+", manual_schedule) if x.strip()]

    try:
        if draft_id:
            existing = models.db_manager.get_campaign_by_id(draft_id)
            if not existing or existing.get("상태") != "임시저장" or str(existing.get("업체ID", "")) != str(client_id):
                return jsonify({"ok": False, "error": "접근 권한이 없습니다."}), 403
            models.db_manager.update_campaign(draft_id, data)
        else:
            campaign_id = str(uuid.uuid4())[:8]
            data["캠페인ID"] = campaign_id
            data["등록일"] = today_str()
            data["완료수량"] = "0"
            draft_id = models.db_manager.create_campaign(data)
            if not draft_id:
                draft_id = campaign_id
        return jsonify({"ok": True, "draft_id": draft_id})
    except Exception as e:
        logger.error("업체 임시저장 에러: %s", e)
        return jsonify({"ok": False, "error": str(e)})


@client_bp.route("/api/campaign/<campaign_id>/draft", methods=["DELETE"])
@client_required
def campaign_draft_delete(campaign_id):
    """업체 임시저장 삭제"""
    client_id = session["client_id"]
    campaign = models.db_manager.get_campaign_by_id(campaign_id)
    if not campaign or campaign.get("상태") != "임시저장":
        return jsonify({"ok": False, "error": "임시저장 캠페인이 아닙니다."}), 404
    if str(campaign.get("업체ID", "")) != str(client_id):
        return jsonify({"ok": False, "error": "접근 권한이 없습니다."}), 403
    ok = models.db_manager.delete_campaign(campaign_id)
    return jsonify({"ok": ok})


# ─── 캠페인 상세 ───

@client_bp.route("/campaign/<campaign_id>")
@client_required
def campaign_detail(campaign_id):
    campaign = models.db_manager.get_campaign_by_id(campaign_id)
    if not campaign:
        flash("캠페인을 찾을 수 없습니다.")
        return redirect(url_for("client.dashboard"))

    # 본인 캠페인인지 확인
    if safe_int(campaign.get("업체ID", 0)) != session["client_id"]:
        flash("접근 권한이 없습니다.")
        return redirect(url_for("client.dashboard"))

    # 진행 현황 통계
    progress_stats = {}
    cid = campaign.get("캠페인ID", campaign_id)
    rows = models.db_manager._fetchall(
        """SELECT status, COUNT(*) as cnt FROM progress
           WHERE campaign_id = %s GROUP BY status""",
        (cid,)
    )
    for r in rows:
        progress_stats[r["status"]] = r["cnt"]

    total_progress = sum(progress_stats.values())
    total_qty = safe_int(campaign.get("총수량", 0))
    progress_pct = round(total_progress / total_qty * 100) if total_qty > 0 else 0

    # 진행건 목록 (리뷰비/입금금액 제외)
    progress_list = models.db_manager._fetchall(
        """SELECT p.id,
                  TO_CHAR(p.created_at AT TIME ZONE 'Asia/Seoul', 'MM/DD') as date_str,
                  c.product_name, r.name as reviewer_name,
                  p.recipient_name, p.phone, p.bank, p.account, p.depositor,
                  p.store_id, p.order_number, p.address, p.nickname,
                  p.status, p.purchase_date, p.purchase_capture_url,
                  p.review_deadline, p.review_submit_date, p.review_capture_url,
                  p.ai_review_result, p.ai_review_reason, p.remark
           FROM progress p
           LEFT JOIN campaigns c ON c.id = p.campaign_id
           LEFT JOIN reviewers r ON r.id = p.reviewer_id
           WHERE p.campaign_id = %s
           ORDER BY p.created_at DESC""",
        (cid,)
    )

    return render_template("client/campaign_detail.html",
                           campaign=campaign,
                           progress_stats=progress_stats,
                           total_progress=total_progress,
                           progress_pct=progress_pct,
                           progress_list=progress_list,
                           company_name=session.get("client_company", ""))


# ─── 전체 진행건 API ───

@client_bp.route("/api/all-progress")
@client_required
def api_all_progress():
    """업체의 모든 캠페인 진행건 목록"""
    client_id = session["client_id"]
    rows = models.db_manager._fetchall(
        """SELECT p.id,
                  TO_CHAR(p.created_at AT TIME ZONE 'Asia/Seoul', 'MM/DD') as date_str,
                  COALESCE(NULLIF(c.campaign_name, ''), c.product_name, '') as campaign_name,
                  c.product_name, r.name as reviewer_name,
                  p.recipient_name, p.phone, p.bank, p.account, p.depositor,
                  p.store_id, p.order_number, p.address, p.nickname,
                  p.status, p.purchase_date, p.purchase_capture_url,
                  p.review_deadline, p.review_submit_date, p.review_capture_url,
                  p.ai_review_result, p.ai_review_reason, p.remark
           FROM progress p
           LEFT JOIN campaigns c ON c.id = p.campaign_id
           LEFT JOIN reviewers r ON r.id = p.reviewer_id
           WHERE c.client_id = %s
           ORDER BY p.created_at DESC""",
        (client_id,)
    )
    result = []
    for p in rows:
        result.append({
            "id": p["id"],
            "date_str": p["date_str"] or "-",
            "campaign_name": p["campaign_name"] or "-",
            "product_name": p["product_name"] or "-",
            "recipient_name": p["recipient_name"] or "-",
            "phone": p["phone"] or "-",
            "store_id": p["store_id"] or "-",
            "order_number": p["order_number"] or "-",
            "address": p["address"] or "-",
            "status": p["status"] or "-",
            "purchase_date": str(p["purchase_date"]) if p["purchase_date"] else "-",
            "purchase_capture_url": p["purchase_capture_url"] or "",
            "review_deadline": str(p["review_deadline"]) if p["review_deadline"] else "-",
            "review_capture_url": p["review_capture_url"] or "",
            "ai_review_result": p["ai_review_result"] or "",
            "ai_review_reason": p["ai_review_reason"] or "",
            "remark": p["remark"] or "-",
        })
    return jsonify(ok=True, data=result)


# ─── 카톡 발송 API ───

@client_bp.route("/api/send-message", methods=["POST"])
@client_required
def api_send_message():
    """업체가 리뷰어에게 카톡 메시지 발송"""
    data = request.get_json() or {}
    progress_id = safe_int(data.get("progress_id"))
    message = (data.get("message") or "").strip()

    if not progress_id or not message:
        return jsonify(ok=False, error="진행건 ID와 메시지를 입력해주세요."), 400

    # 진행건 조회 → 캠페인 소유 확인
    row = models.db_manager._fetchone(
        """SELECT p.campaign_id, c.client_id
           FROM progress p
           JOIN campaigns c ON c.id = p.campaign_id
           WHERE p.id = %s""",
        (progress_id,)
    )
    if not row:
        return jsonify(ok=False, error="진행건을 찾을 수 없습니다."), 404

    if safe_int(row.get("client_id")) != session["client_id"]:
        return jsonify(ok=False, error="접근 권한이 없습니다."), 403

    # 카카오톡 발송
    if not models.kakao_notifier:
        return jsonify(ok=False, error="알림 시스템이 초기화되지 않았습니다."), 500

    # 리뷰어 정보 먼저 확인
    info = models.kakao_notifier._get_progress_info(progress_id)
    if not info.get("name") or not info.get("phone"):
        logger.warning("카톡 발송 실패 - 리뷰어 정보 없음: progress_id=%s, info=%s", progress_id, info)
        return jsonify(ok=False, error="리뷰어 연락처 정보가 없습니다."), 400

    result = models.kakao_notifier.send_reminder(progress_id, custom_message=message)
    if result:
        return jsonify(ok=True)
    else:
        logger.warning("카톡 발송 실패 - 전송 에러: progress_id=%s, reviewer=%s %s",
                       progress_id, info.get("name"), info.get("phone"))
        return jsonify(ok=False, error="카톡 발송에 실패했습니다. 서버 연결을 확인해주세요."), 500


# ── AI 대화형 캠페인 등록 ──────────────────

@client_bp.route("/ai-register")
@client_required
def ai_register():
    return render_template("client/ai_register.html", company_name=session.get("client_company", ""))


@client_bp.route("/api/ai-chat", methods=["POST"])
@client_required
def api_ai_chat():
    from modules.ai_campaign_chat import get_chat_engine
    data = request.get_json()
    messages = data.get("messages", [])
    try:
        engine = get_chat_engine()
        result = engine.chat(messages, portal="client", owner_id=session.get("client_id"))
        return jsonify({"ok": True, "reply": result["reply"], "messages": result["messages"]})
    except Exception as e:
        logger.error(f"AI chat error: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)})


@client_bp.route("/api/ai-chat/reset", methods=["POST"])
@client_required
def api_ai_chat_reset():
    return jsonify({"ok": True})


@client_bp.route("/api/ai-chat/upload-image", methods=["POST"])
@client_required
def api_ai_chat_upload_image():
    file = request.files.get("image")
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "이미지 파일이 필요합니다"})
    try:
        if models.drive_uploader:
            url = models.drive_uploader.upload_from_flask_file(
                file, capture_type="purchase",
                description="AI채팅 상품이미지"
            )
            return jsonify({"ok": True, "url": url})
        return jsonify({"ok": False, "error": "Drive 업로더를 사용할 수 없습니다"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

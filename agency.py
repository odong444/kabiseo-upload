"""
agency.py - 대행사 포털 라우트

대행사가 소속 클라이언트의 캠페인을 관리하고, 승인/반려하는 포털.
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

agency_bp = Blueprint("agency", __name__, url_prefix="/agency")


# ─── 인증 ───

def agency_required(f):
    """대행사 로그인 데코레이터"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("agency_id"):
            return redirect(url_for("agency.login"))
        return f(*args, **kwargs)
    return decorated


@agency_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if session.get("agency_id"):
            return redirect(url_for("agency.dashboard"))
        return render_template("agency/login.html")

    login_id = request.form.get("login_id", "").strip()
    password = request.form.get("password", "").strip()

    if not login_id or not password:
        flash("아이디와 비밀번호를 입력해주세요.")
        return redirect(url_for("agency.login"))

    agency = models.db_manager.get_agency_by_login(login_id)
    if not agency:
        flash("아이디 또는 비밀번호가 올바르지 않습니다.")
        return redirect(url_for("agency.login"))

    if not agency.get("is_active", True):
        flash("비활성화된 계정입니다. 담당자에게 문의해주세요.")
        return redirect(url_for("agency.login"))

    from werkzeug.security import check_password_hash
    if not check_password_hash(agency["password_hash"], password):
        flash("아이디 또는 비밀번호가 올바르지 않습니다.")
        return redirect(url_for("agency.login"))

    session["agency_id"] = agency["id"]
    session["agency_company"] = agency["company_name"]
    session["agency_login_id"] = agency["login_id"]
    return redirect(url_for("agency.dashboard"))


@agency_bp.route("/logout")
def logout():
    session.pop("agency_id", None)
    session.pop("agency_company", None)
    session.pop("agency_login_id", None)
    return redirect(url_for("agency.login"))


# ─── 대시보드 ───

@agency_bp.route("/dashboard")
@agency_required
def dashboard():
    agency_id = session["agency_id"]
    campaigns = models.db_manager.get_agency_campaigns(agency_id)
    stats = models.db_manager.get_agency_campaign_stats(agency_id)
    clients = models.db_manager.get_agency_clients(agency_id)

    # 각 캠페인의 구매/리뷰 진행률
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
    drafts = models.db_manager.get_drafts("agency", agency_id)

    return render_template("agency/dashboard.html",
                           campaigns=campaigns, stats=stats, clients=clients,
                           drafts=drafts,
                           company_name=session.get("agency_company", ""))


# ─── 클라이언트 관리 ───

@agency_bp.route("/clients")
@agency_required
def clients():
    agency_id = session["agency_id"]
    client_list = models.db_manager.get_agency_clients(agency_id)
    return render_template("agency/clients.html",
                           clients=client_list,
                           company_name=session.get("agency_company", ""))


@agency_bp.route("/api/client", methods=["POST"])
@agency_required
def api_client_create():
    if not models.db_manager:
        return jsonify({"ok": False, "error": "시스템 초기화 중"})
    agency_id = session["agency_id"]
    data = request.get_json(silent=True) or {}
    login_id = data.get("login_id", "").strip()
    password = data.get("password", "").strip()
    company_name = data.get("company_name", "").strip()
    if not login_id or not password or not company_name:
        return jsonify({"ok": False, "error": "아이디, 비밀번호, 업체명은 필수입니다."})
    from werkzeug.security import generate_password_hash
    try:
        cid = models.db_manager.create_client(
            login_id=login_id,
            password_hash=generate_password_hash(password),
            company_name=company_name,
            contact_name=data.get("contact_name", "").strip(),
            contact_phone=data.get("contact_phone", "").strip(),
            contact_email=data.get("contact_email", "").strip(),
            memo=data.get("memo", "").strip(),
            agency_id=agency_id,
        )
        return jsonify({"ok": True, "id": cid})
    except Exception as e:
        logger.error("대행사 클라이언트 생성 에러: %s", e)
        return jsonify({"ok": False, "error": str(e)})


@agency_bp.route("/api/client/<int:client_id>", methods=["PUT"])
@agency_required
def api_client_update(client_id):
    if not models.db_manager:
        return jsonify({"ok": False, "error": "시스템 초기화 중"})
    agency_id = session["agency_id"]
    # 소속 확인
    client = models.db_manager.get_client_by_id(client_id)
    if not client or safe_int(client.get("agency_id")) != agency_id:
        return jsonify({"ok": False, "error": "접근 권한이 없습니다."})
    data = request.get_json(silent=True) or {}
    if "password" in data:
        pw = data.pop("password")
        if pw.strip():
            from werkzeug.security import generate_password_hash
            data["password_hash"] = generate_password_hash(pw)
    # agency_id 변경 방지
    data.pop("agency_id", None)
    try:
        models.db_manager.update_client(client_id, **data)
        return jsonify({"ok": True})
    except Exception as e:
        logger.error("대행사 클라이언트 수정 에러: %s", e)
        return jsonify({"ok": False, "error": str(e)})


@agency_bp.route("/api/client/<int:client_id>", methods=["DELETE"])
@agency_required
def api_client_delete(client_id):
    if not models.db_manager:
        return jsonify({"ok": False, "error": "시스템 초기화 중"})
    agency_id = session["agency_id"]
    client = models.db_manager.get_client_by_id(client_id)
    if not client or safe_int(client.get("agency_id")) != agency_id:
        return jsonify({"ok": False, "error": "접근 권한이 없습니다."})
    try:
        models.db_manager.delete_client(client_id)
        return jsonify({"ok": True})
    except Exception as e:
        logger.error("대행사 클라이언트 삭제 에러: %s", e)
        return jsonify({"ok": False, "error": str(e)})


# ─── 캠페인 생성 ───

@agency_bp.route("/campaign/new", methods=["GET"])
@agency_required
def campaign_new():
    agency_id = session["agency_id"]
    clients = models.db_manager.get_agency_clients(agency_id)

    # 임시저장 불러오기
    draft = None
    draft_id = request.args.get("draft")
    if draft_id:
        draft = models.db_manager.get_campaign_by_id(draft_id)
        if draft and (draft.get("상태") != "임시저장" or str(draft.get("대행사ID", "")) != str(agency_id)):
            draft = None

    return render_template("agency/campaign_request.html",
                           clients=clients, draft=draft,
                           company_name=session.get("agency_company", ""))


@agency_bp.route("/campaign/new", methods=["POST"])
@agency_required
def campaign_new_post():
    if not models.db_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("agency.dashboard"))

    agency_id = session["agency_id"]
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
            "상태": "대행사승인",
            "등록일": today_str(),
            "대행사ID": agency_id,
        }
    else:
        campaign_id = str(uuid.uuid4())[:8]
        data = {
            "캠페인ID": campaign_id,
            "등록일": today_str(),
            "상태": "대행사승인",  # 대행사가 생성하면 대행사승인 상태 (관리자 최종 승인 대기)
            "완료수량": "0",
            "대행사ID": agency_id,
        }

    # 클라이언트 선택 (선택사항)
    client_id = safe_int(request.form.get("client_id", ""))
    if client_id:
        # 소속 클라이언트인지 확인
        client = models.db_manager.get_client_by_id(client_id)
        if client and safe_int(client.get("agency_id")) == agency_id:
            data["업체ID"] = client_id
            if not request.form.get("업체명", "").strip():
                data["업체명"] = client.get("company_name", "")

    for field in fields:
        val = request.form.get(field, "").strip()
        if val:
            data[field] = val

    if not data.get("업체명"):
        data["업체명"] = session.get("agency_company", "")

    # 상품코드 자동 추출
    product_link = data.get("상품링크", "")
    if product_link:
        codes = extract_product_codes(product_link)
        if codes:
            data["상품코드"] = codes

    # 일정 생성
    start_date = request.form.get("시작일", "").strip() or today_str()
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

    # 상품이미지
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
        flash(f"캠페인 '{display_name}' 요청이 접수되었습니다. 관리자 승인 후 진행됩니다.")
    except Exception as e:
        logger.error(f"캠페인 생성 에러: {e}")
        flash(f"요청 중 오류가 발생했습니다: {e}")

    return redirect(url_for("agency.dashboard"))


# ─── 캠페인 임시저장 ───

@agency_bp.route("/campaign/draft", methods=["POST"])
@agency_required
def campaign_draft_save():
    """대행사 캠페인 임시저장"""
    if not models.db_manager:
        return jsonify({"ok": False, "error": "시스템 초기화 중"})

    agency_id = session["agency_id"]
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
    data["대행사ID"] = agency_id

    # 클라이언트 선택
    client_id = safe_int(request.form.get("client_id", ""))
    if client_id:
        client = models.db_manager.get_client_by_id(client_id)
        if client and safe_int(client.get("agency_id")) == agency_id:
            data["업체ID"] = client_id
            if not data.get("업체명"):
                data["업체명"] = client.get("company_name", "")

    # 시작일/일정
    start_date = request.form.get("시작일", "").strip()
    if start_date:
        data["시작일"] = start_date
    manual_schedule = request.form.get("일정", "").strip()
    if manual_schedule:
        data["일정"] = [safe_int(x) for x in re.split(r"[,\s]+", manual_schedule) if x.strip()]

    try:
        if draft_id:
            # 기존 임시저장 업데이트 — 소유권 확인
            existing = models.db_manager.get_campaign_by_id(draft_id)
            if not existing or existing.get("상태") != "임시저장" or str(existing.get("대행사ID", "")) != str(agency_id):
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
        logger.error("대행사 임시저장 에러: %s", e)
        return jsonify({"ok": False, "error": str(e)})


@agency_bp.route("/api/campaign/<campaign_id>/draft", methods=["DELETE"])
@agency_required
def campaign_draft_delete(campaign_id):
    """대행사 임시저장 삭제"""
    agency_id = session["agency_id"]
    campaign = models.db_manager.get_campaign_by_id(campaign_id)
    if not campaign or campaign.get("상태") != "임시저장":
        return jsonify({"ok": False, "error": "임시저장 캠페인이 아닙니다."}), 404
    if str(campaign.get("대행사ID", "")) != str(agency_id):
        return jsonify({"ok": False, "error": "접근 권한이 없습니다."}), 403
    ok = models.db_manager.delete_campaign(campaign_id)
    return jsonify({"ok": ok})


# ─── 캠페인 상세 ───

@agency_bp.route("/campaign/<campaign_id>")
@agency_required
def campaign_detail(campaign_id):
    agency_id = session["agency_id"]
    campaign = models.db_manager.get_campaign_by_id(campaign_id)
    if not campaign:
        flash("캠페인을 찾을 수 없습니다.")
        return redirect(url_for("agency.dashboard"))

    # 권한 확인: 대행사가 직접 생성했거나 소속 클라이언트의 캠페인
    camp_agency = safe_int(campaign.get("대행사ID", 0))
    camp_client = safe_int(campaign.get("업체ID", 0))
    is_mine = (camp_agency == agency_id)
    if not is_mine and camp_client:
        client = models.db_manager.get_client_by_id(camp_client)
        if client and safe_int(client.get("agency_id")) == agency_id:
            is_mine = True
    if not is_mine:
        flash("접근 권한이 없습니다.")
        return redirect(url_for("agency.dashboard"))

    # 진행 현황
    cid = campaign.get("캠페인ID", campaign_id)
    progress_stats = {}
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

    return render_template("agency/campaign_detail.html",
                           campaign=campaign,
                           progress_stats=progress_stats,
                           total_progress=total_progress,
                           progress_pct=progress_pct,
                           progress_list=progress_list,
                           company_name=session.get("agency_company", ""))


# ─── 전체 진행건 API ───

@agency_bp.route("/api/all-progress")
@agency_required
def api_all_progress():
    """대행사의 모든 캠페인 진행건 목록"""
    agency_id = session["agency_id"]
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
           WHERE c.agency_id = %s
              OR c.client_id IN (SELECT id FROM clients WHERE agency_id = %s)
           ORDER BY p.created_at DESC""",
        (agency_id, agency_id)
    )
    result = []
    for p in rows:
        result.append({
            "id": p["id"],
            "date_str": p["date_str"] or "-",
            "campaign_name": p["campaign_name"] or "-",
            "product_name": p["product_name"] or "-",
            "recipient_name": p["recipient_name"] or p["reviewer_name"] or "-",
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


# ─── 승인/반려 API ───

@agency_bp.route("/api/campaign/<campaign_id>/approve", methods=["POST"])
@agency_required
def api_campaign_approve(campaign_id):
    agency_id = session["agency_id"]
    campaign = models.db_manager.get_campaign_by_id(campaign_id)
    if not campaign:
        return jsonify({"ok": False, "error": "캠페인을 찾을 수 없습니다."})

    # 권한 확인
    camp_client = safe_int(campaign.get("업체ID", 0))
    if camp_client:
        client = models.db_manager.get_client_by_id(camp_client)
        if not client or safe_int(client.get("agency_id")) != agency_id:
            return jsonify({"ok": False, "error": "접근 권한이 없습니다."})
    else:
        return jsonify({"ok": False, "error": "클라이언트 캠페인이 아닙니다."})

    if campaign.get("상태") != "승인대기":
        return jsonify({"ok": False, "error": "승인대기 상태가 아닙니다."})

    models.db_manager.update_campaign(campaign_id, {"상태": "대행사승인", "대행사ID": agency_id})
    logger.info("대행사 캠페인 승인: %s (agency_id=%s)", campaign_id, agency_id)
    return jsonify({"ok": True})


@agency_bp.route("/api/campaign/<campaign_id>/reject", methods=["POST"])
@agency_required
def api_campaign_reject(campaign_id):
    agency_id = session["agency_id"]
    campaign = models.db_manager.get_campaign_by_id(campaign_id)
    if not campaign:
        return jsonify({"ok": False, "error": "캠페인을 찾을 수 없습니다."})

    camp_client = safe_int(campaign.get("업체ID", 0))
    if camp_client:
        client = models.db_manager.get_client_by_id(camp_client)
        if not client or safe_int(client.get("agency_id")) != agency_id:
            return jsonify({"ok": False, "error": "접근 권한이 없습니다."})
    else:
        return jsonify({"ok": False, "error": "클라이언트 캠페인이 아닙니다."})

    if campaign.get("상태") != "승인대기":
        return jsonify({"ok": False, "error": "승인대기 상태가 아닙니다."})

    data = request.get_json(silent=True) or {}
    reason = data.get("reason", "").strip()
    models.db_manager.update_campaign(campaign_id, {"상태": "반려", "반려사유": reason})
    logger.info("대행사 캠페인 반려: %s, 사유: %s", campaign_id, reason)
    return jsonify({"ok": True})


# ── AI 대화형 캠페인 등록 ──────────────────

@agency_bp.route("/ai-register")
@agency_required
def ai_register():
    return render_template("agency/ai_register.html", company_name=session.get("agency_company", ""))


@agency_bp.route("/api/ai-chat", methods=["POST"])
@agency_required
def api_ai_chat():
    from modules.ai_campaign_chat import get_chat_engine
    data = request.get_json()
    messages = data.get("messages", [])
    try:
        engine = get_chat_engine()
        result = engine.chat(messages, portal="agency", owner_id=session.get("agency_id"))
        return jsonify({"ok": True, "reply": result["reply"], "messages": result["messages"]})
    except Exception as e:
        logger.error(f"AI chat error: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)})


@agency_bp.route("/api/ai-chat/reset", methods=["POST"])
@agency_required
def api_ai_chat_reset():
    return jsonify({"ok": True})


@agency_bp.route("/api/ai-chat/upload-image", methods=["POST"])
@agency_required
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

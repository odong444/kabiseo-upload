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

    # 각 캠페인에 진행률 추가
    for c in campaigns:
        cid = c.get("캠페인ID", "")
        total = safe_int(c.get("총수량", 0))
        done = safe_int(c.get("완료수량", 0))
        c["progress_pct"] = round(done / total * 100) if total > 0 else 0

    return render_template("client/dashboard.html",
                           campaigns=campaigns, stats=stats,
                           company_name=session.get("client_company", ""))


# ─── 캠페인 요청 ───

@client_bp.route("/campaign/new", methods=["GET"])
@client_required
def campaign_new():
    return render_template("client/campaign_request.html",
                           company_name=session.get("client_company", ""))


@client_bp.route("/campaign/new", methods=["POST"])
@client_required
def campaign_new_post():
    if not models.db_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("client.dashboard"))

    campaign_id = str(uuid.uuid4())[:8]

    fields = [
        "캠페인유형", "플랫폼", "업체명", "캠페인명", "상품명",
        "총수량", "일수량", "진행일수", "1인일일제한",
        "상품금액", "리뷰비", "중복허용", "구매가능시간", "캠페인가이드",
        "상품링크", "옵션", "키워드", "유입방식", "리뷰기한일수",
    ]

    data = {
        "캠페인ID": campaign_id,
        "등록일": today_str(),
        "상태": "승인대기",
        "완료수량": "0",
        "업체ID": session["client_id"],
    }
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
        models.db_manager.create_campaign(data)
        display_name = data.get("캠페인명", "").strip() or data["상품명"]
        flash(f"캠페인 '{display_name}' 요청이 접수되었습니다. 담당자 승인 후 진행됩니다.")
    except Exception as e:
        logger.error(f"캠페인 요청 에러: {e}")
        flash(f"요청 중 오류가 발생했습니다: {e}")

    return redirect(url_for("client.dashboard"))


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

    return render_template("client/campaign_detail.html",
                           campaign=campaign,
                           progress_stats=progress_stats,
                           total_progress=total_progress,
                           progress_pct=progress_pct,
                           company_name=session.get("client_company", ""))

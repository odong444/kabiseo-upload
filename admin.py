"""
admin.py - 관리자 대시보드 라우트
"""

import os
import logging
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, flash

import models

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin1234")


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin.login"))
        return f(*args, **kwargs)
    return decorated


# ──────── 인증 ────────

@admin_bp.route("/")
@admin_bp.route("/login", methods=["GET"])
def login():
    if session.get("admin_logged_in"):
        return redirect(url_for("admin.dashboard"))
    return render_template("admin/login.html")


@admin_bp.route("/login", methods=["POST"])
def login_post():
    password = request.form.get("password", "")
    if password == ADMIN_PASSWORD:
        session["admin_logged_in"] = True
        return redirect(url_for("admin.dashboard"))
    flash("비밀번호가 올바르지 않습니다.")
    return redirect(url_for("admin.login"))


@admin_bp.route("/logout")
def logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin.login"))


# ──────── 대시보드 ────────

@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    stats = {}
    recent_messages = []
    if models.sheets_manager:
        stats = models.sheets_manager.get_today_stats()
    recent_messages = models.chat_logger.get_recent_messages(20)
    return render_template("admin/dashboard.html", stats=stats, recent_messages=recent_messages)


# ──────── 캠페인 관리 ────────

@admin_bp.route("/campaigns")
@admin_required
def campaigns():
    campaign_list = []
    if models.campaign_manager:
        campaign_list = models.campaign_manager.get_all_campaigns()
    return render_template("admin/campaigns.html", campaigns=campaign_list)


@admin_bp.route("/campaigns/<int:row>/edit", methods=["GET"])
@admin_required
def campaign_edit(row):
    if not models.sheets_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.campaigns"))

    all_campaigns = models.sheets_manager.get_all_campaigns()
    campaign = None
    for c in all_campaigns:
        if c.get("_row_idx") == row:
            campaign = c
            break

    if not campaign:
        flash("캠페인을 찾을 수 없습니다.")
        return redirect(url_for("admin.campaigns"))

    return render_template("admin/campaign_edit.html", campaign=campaign, row=row)


@admin_bp.route("/campaigns/<int:row>/edit", methods=["POST"])
@admin_required
def campaign_edit_post(row):
    if not models.sheets_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.campaigns"))

    editable_fields = [
        "상태", "업체명", "상품명", "상품링크", "옵션", "키워드",
        "유입방식", "총수량", "일수량", "완료수량",
        "택배사", "리뷰제공", "리뷰기한일수", "리뷰비", "중복허용", "메모"
    ]

    for field_name in editable_fields:
        value = request.form.get(field_name, "").strip()
        try:
            models.sheets_manager.update_campaign_cell(row, field_name, value)
        except Exception as e:
            logger.error(f"캠페인 수정 에러 ({field_name}): {e}")

    flash("캠페인이 수정되었습니다.")
    return redirect(url_for("admin.campaigns"))


# ──────── 대화 이력 ────────

@admin_bp.route("/chat/<reviewer_id>")
@admin_required
def chat_viewer(reviewer_id):
    history = models.chat_logger.get_history(reviewer_id)
    return render_template("admin/chat_viewer.html", reviewer_id=reviewer_id, history=history)


@admin_bp.route("/chat")
@admin_required
def chat_list():
    reviewer_ids = models.chat_logger.get_all_reviewer_ids()
    q = request.args.get("q", "").strip()
    if q:
        reviewer_ids = [r for r in reviewer_ids if q.lower() in r.lower()]
    return render_template("admin/chat_viewer.html", reviewer_ids=reviewer_ids, reviewer_id=None, history=[], q=q)


# ──────── 정산 관리 ────────

@admin_bp.route("/settlement")
@admin_required
def settlement():
    items = []
    if models.sheets_manager:
        all_items = models.sheets_manager.get_all_reviewers()
        items = [i for i in all_items if i.get("상태") == "리뷰제출"]
    return render_template("admin/settlement.html", items=items)


@admin_bp.route("/settlement/process", methods=["POST"])
@admin_required
def settlement_process():
    if not models.sheets_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.settlement"))

    row_indices = request.form.getlist("row_idx")
    amount = request.form.get("amount", "0")

    processed = 0
    for row_str in row_indices:
        try:
            row_idx = int(row_str)
            models.sheets_manager.process_settlement(row_idx, amount)
            processed += 1
        except Exception as e:
            logger.error(f"정산 처리 에러 (row {row_str}): {e}")

    flash(f"{processed}건 정산 처리 완료")
    return redirect(url_for("admin.settlement"))


# ──────── 리뷰어 목록 ────────

@admin_bp.route("/reviewers")
@admin_required
def reviewers():
    items = []
    if models.sheets_manager:
        items = models.sheets_manager.get_all_reviewers()
    q = request.args.get("q", "").strip()
    if q:
        ql = q.lower()
        items = [
            i for i in items
            if ql in i.get("이름", "").lower()
            or ql in i.get("연락처", "")
            or ql in i.get("아이디", "").lower()
        ]
    return render_template("admin/dashboard.html", stats={}, recent_messages=[], reviewers=items, q=q, show_reviewers=True)


# ──────── API (AJAX) ────────

@admin_bp.route("/api/rate", methods=["POST"])
@admin_required
def rate_message():
    data = request.get_json(silent=True) or {}
    reviewer_id = data.get("reviewer_id", "")
    timestamp = float(data.get("timestamp", 0))
    rating = data.get("rating", "")
    ok = models.chat_logger.rate_message(reviewer_id, timestamp, rating)
    return jsonify({"ok": ok})

"""
admin.py - 관리자 대시보드 라우트
"""

import os
import io
import csv
import logging
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, flash, Response

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
        "상태", "상품명", "업체명", "플랫폼", "캠페인유형",
        "상품금액", "리뷰비", "결제금액",
        "총수량", "일수량", "완료수량",
        "구매가능시간", "중복허용",
        "상품링크", "키워드", "유입방식", "리뷰기한일수",
        "공개여부", "캠페인가이드", "메모",
    ]

    for field_name in editable_fields:
        value = request.form.get(field_name, "").strip()
        try:
            models.sheets_manager.update_campaign_cell(row, field_name, value)
        except Exception as e:
            logger.error(f"캠페인 수정 에러 ({field_name}): {e}")

    flash("캠페인이 수정되었습니다.")
    return redirect(url_for("admin.campaigns"))


# ──────── 캠페인 신규 등록 ────────

@admin_bp.route("/campaigns/new", methods=["GET"])
@admin_required
def campaign_new():
    return render_template("admin/campaign_new.html")


@admin_bp.route("/campaigns/new", methods=["POST"])
@admin_required
def campaign_new_post():
    if not models.sheets_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.campaigns"))

    import uuid
    from modules.utils import today_str

    campaign_id = str(uuid.uuid4())[:8]

    # 간소화된 필드
    fields = [
        "캠페인유형", "플랫폼", "업체명", "상품명",
        "총수량", "일수량", "진행일수",
        "상품금액", "리뷰비", "중복허용", "구매가능시간", "캠페인가이드",
    ]

    data = {"캠페인ID": campaign_id, "등록일": today_str(), "상태": "모집중", "완료수량": "0"}
    for field in fields:
        data[field] = request.form.get(field, "").strip()

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

    # 새 컬럼 확보
    models.sheets_manager.ensure_campaign_columns(["진행일수", "캠페인가이드"])

    # 시트에 행 추가
    try:
        ws = models.sheets_manager.spreadsheet.worksheet("캠페인관리")
        headers = ws.row_values(1)
        new_row = []
        for h in headers:
            new_row.append(data.get(h, ""))
        ws.append_row(new_row, value_input_option="USER_ENTERED")
        flash(f"캠페인 '{data['상품명']}' 등록 완료 (ID: {campaign_id})")
    except Exception as e:
        logger.error(f"캠페인 등록 에러: {e}")
        flash(f"등록 중 오류가 발생했습니다: {e}")

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


# ──────── 리뷰 검수 ────────

def _notify_reviewer_reject(row_data: dict, reason: str):
    """반려 시 리뷰어에게 채팅 알림 전송"""
    reviewer_name = row_data.get("진행자이름", "") or row_data.get("수취인명", "")
    reviewer_phone = row_data.get("진행자연락처", "") or row_data.get("연락처", "")
    if reviewer_name and reviewer_phone:
        rid = f"{reviewer_name}_{reviewer_phone}"
        msg = f"리뷰 검수 반려: {reason}\n리뷰 캡쳐를 다시 제출해주세요."
        if models.chat_logger:
            models.chat_logger.log(rid, "bot", msg)
        if models.timeout_manager and models.timeout_manager._socketio:
            models.timeout_manager._socketio.emit("bot_message", {"message": msg}, room=rid)


def _sort_by_date_asc(items, date_key="날짜"):
    """날짜 오름차순 정렬 (오래된 것 먼저)"""
    def sort_key(item):
        return item.get(date_key, "") or "9999"
    return sorted(items, key=sort_key)


@admin_bp.route("/reviews")
@admin_required
def reviews():
    items = []
    if models.sheets_manager:
        all_items = models.sheets_manager.get_all_reviewers()
        items = [i for i in all_items if i.get("상태") == "리뷰제출"]
    items = _sort_by_date_asc(items)
    return render_template("admin/reviews.html", items=items)


@admin_bp.route("/reviews/approve", methods=["POST"])
@admin_required
def reviews_approve():
    if not models.sheets_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.reviews"))

    row_indices = request.form.getlist("row_idx")
    processed = 0
    for row_str in row_indices:
        try:
            row_idx = int(row_str)
            models.sheets_manager.approve_review(row_idx)
            processed += 1
        except Exception as e:
            logger.error(f"검수 승인 에러 (row {row_str}): {e}")

    flash(f"{processed}건 승인 완료 (입금대기)")
    return redirect(url_for("admin.reviews"))


@admin_bp.route("/reviews/reject", methods=["POST"])
@admin_required
def reviews_reject():
    if not models.sheets_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.reviews"))

    row_indices = request.form.getlist("row_idx")
    reason = request.form.get("reason", "").strip() or "리뷰 사진을 다시 확인해주세요."
    processed = 0

    for row_str in row_indices:
        try:
            row_idx = int(row_str)
            row_data = models.sheets_manager.get_row_dict(row_idx)
            models.sheets_manager.reject_review(row_idx, reason)
            processed += 1
            _notify_reviewer_reject(row_data, reason)
        except Exception as e:
            logger.error(f"검수 반려 에러 (row {row_str}): {e}")

    flash(f"{processed}건 반려 완료")
    return redirect(url_for("admin.reviews"))


# 검수 AJAX API (모달에서 사용)
@admin_bp.route("/api/reviews/approve", methods=["POST"])
@admin_required
def api_reviews_approve():
    if not models.sheets_manager:
        return jsonify({"ok": False, "message": "시스템 초기화 중"})
    data = request.get_json(silent=True) or {}
    row_idx = data.get("row_idx")
    try:
        models.sheets_manager.approve_review(int(row_idx))
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"검수 승인 API 에러: {e}")
        return jsonify({"ok": False, "message": str(e)})


@admin_bp.route("/api/reviews/reject", methods=["POST"])
@admin_required
def api_reviews_reject():
    if not models.sheets_manager:
        return jsonify({"ok": False, "message": "시스템 초기화 중"})
    data = request.get_json(silent=True) or {}
    row_idx = data.get("row_idx")
    reason = data.get("reason", "").strip() or "리뷰 사진을 다시 확인해주세요."
    try:
        row_data = models.sheets_manager.get_row_dict(int(row_idx))
        models.sheets_manager.reject_review(int(row_idx), reason)
        _notify_reviewer_reject(row_data, reason)
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"검수 반려 API 에러: {e}")
        return jsonify({"ok": False, "message": str(e)})


# ──────── 정산 관리 ────────

@admin_bp.route("/settlement")
@admin_required
def settlement():
    items = []
    if models.sheets_manager:
        all_items = models.sheets_manager.get_all_reviewers()
        items = [i for i in all_items if i.get("상태") == "입금대기"]
    # 리뷰제출일 오름차순 (오래된 것 먼저)
    items = _sort_by_date_asc(items, "리뷰제출일")
    return render_template("admin/settlement.html", items=items)


@admin_bp.route("/settlement/process", methods=["POST"])
@admin_required
def settlement_process():
    if not models.sheets_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.settlement"))

    row_indices = request.form.getlist("row_idx")
    processed = 0
    for row_str in row_indices:
        try:
            row_idx = int(row_str)
            # 기존 입금금액 사용
            row_data = models.sheets_manager.get_row_dict(row_idx)
            amount = row_data.get("입금금액", "0") or "0"
            models.sheets_manager.process_settlement(row_idx, amount)
            processed += 1
        except Exception as e:
            logger.error(f"정산 처리 에러 (row {row_str}): {e}")

    flash(f"{processed}건 정산 처리 완료")
    return redirect(url_for("admin.settlement"))


@admin_bp.route("/settlement/download")
@admin_required
def settlement_download():
    """입금대기 목록 엑셀(CSV) 다운로드"""
    items = []
    if models.sheets_manager:
        all_items = models.sheets_manager.get_all_reviewers()
        items = [i for i in all_items if i.get("상태") == "입금대기"]
    items = _sort_by_date_asc(items, "리뷰제출일")

    output = io.StringIO()
    output.write('\ufeff')  # UTF-8 BOM for Excel
    writer = csv.writer(output)
    writer.writerow(["수취인명", "연락처", "은행", "계좌", "예금주", "아이디", "제품명", "입금금액", "리뷰제출일"])
    for item in items:
        writer.writerow([
            item.get("수취인명", ""),
            item.get("연락처", ""),
            item.get("은행", ""),
            item.get("계좌", ""),
            item.get("예금주", ""),
            item.get("아이디", ""),
            item.get("제품명", ""),
            item.get("입금금액", ""),
            item.get("리뷰제출일", ""),
        ])

    from modules.utils import today_str
    filename = f"settlement_{today_str()}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


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


# ──────── 가이드 ────────

@admin_bp.route("/guide")
@admin_required
def guide():
    return render_template("admin/guide.html")


# ──────── 타임아웃 복원 ────────

@admin_bp.route("/reviewers/restore", methods=["POST"])
@admin_required
def reviewers_restore():
    if not models.sheets_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.reviewers"))

    row_indices = request.form.getlist("row_idx")
    processed = 0
    for row_str in row_indices:
        try:
            row_idx = int(row_str)
            models.sheets_manager.restore_from_timeout(row_idx)
            processed += 1
        except Exception as e:
            logger.error(f"타임아웃 복원 에러 (row {row_str}): {e}")

    flash(f"{processed}건 가이드전달 상태로 복원 완료")
    return redirect(url_for("admin.reviewers"))


# ──────── 전체현황 ────────

@admin_bp.route("/overview")
@admin_required
def overview():
    campaigns = []
    if models.campaign_manager:
        campaigns = models.campaign_manager.get_all_campaigns()

    # Calculate stats per campaign
    all_reviewers = []
    if models.sheets_manager:
        all_reviewers = models.sheets_manager.get_all_reviewers()

    # Group by campaign
    campaign_stats = []
    for c in campaigns:
        cid = c.get("캠페인ID", "")
        total = int(c.get("총수량", 0) or 0)
        done = int(c.get("완료수량", 0) or 0)

        # Count review and settlement from reviewer data
        review_done = 0
        settlement_done = 0
        for r in all_reviewers:
            if r.get("캠페인ID") == cid:
                status = r.get("상태", "")
                if status in ("리뷰제출", "입금대기", "입금완료"):
                    review_done += 1
                if status == "입금완료":
                    settlement_done += 1

        rate = round(done / total * 100, 1) if total > 0 else 0
        campaign_stats.append({
            **c,
            "review_done": review_done,
            "settlement_done": settlement_done,
            "rate": rate,
        })

    # Company summary
    companies = {}
    for cs in campaign_stats:
        company = cs.get("업체명", "기타")
        if company not in companies:
            companies[company] = {"count": 0, "total": 0, "done": 0}
        companies[company]["count"] += 1
        companies[company]["total"] += int(cs.get("총수량", 0) or 0)
        companies[company]["done"] += int(cs.get("완료수량", 0) or 0)

    for k, v in companies.items():
        v["rate"] = round(v["done"] / v["total"] * 100, 1) if v["total"] > 0 else 0

    return render_template("admin/overview.html",
                          campaigns=campaign_stats, companies=companies)


# ──────── 활동 로그 ────────

@admin_bp.route("/logs")
@admin_required
def activity_logs():
    logs = []
    if models.activity_logger:
        log_type = request.args.get("type", "")
        logs = models.activity_logger.get_recent_logs(limit=200, log_type=log_type)
    return render_template("admin/logs.html", logs=logs)


# ──────── API (AJAX) ────────

@admin_bp.route("/api/fix-rejected-remarks", methods=["POST"])
@admin_required
def fix_rejected_remarks():
    """반려 건 중 비고가 비어있는 것을 채워주는 일회성 API"""
    if not models.sheets_manager:
        return jsonify({"ok": False, "message": "시스템 초기화 중"})

    ws = models.sheets_manager._get_ws()
    headers = models.sheets_manager._get_headers(ws)
    all_rows = ws.get_all_values()

    status_col = models.sheets_manager._find_col(headers, "상태")
    remark_col = models.sheets_manager._find_col(headers, "비고")
    review_link_col = models.sheets_manager._find_col(headers, "리뷰캡쳐링크")
    review_date_col = models.sheets_manager._find_col(headers, "리뷰제출일")

    if status_col < 0 or remark_col < 0:
        return jsonify({"ok": False, "message": "컬럼 없음"})

    fixed = 0
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) <= max(status_col, remark_col):
            continue
        # 구매내역제출 상태 + 비고 비어있음 + 리뷰캡쳐링크 비어있음 + 리뷰제출일 비어있음 → 반려 건
        if row[status_col] != "구매내역제출":
            continue
        if row[remark_col].strip():
            continue
        has_review_link = review_link_col >= 0 and len(row) > review_link_col and row[review_link_col].strip()
        has_review_date = review_date_col >= 0 and len(row) > review_date_col and row[review_date_col].strip()
        if not has_review_link and not has_review_date:
            ws.update_cell(i, remark_col + 1, "반려")
            fixed += 1

    return jsonify({"ok": True, "fixed": fixed})


@admin_bp.route("/api/rate", methods=["POST"])
@admin_required
def rate_message():
    data = request.get_json(silent=True) or {}
    reviewer_id = data.get("reviewer_id", "")
    timestamp = float(data.get("timestamp", 0))
    rating = data.get("rating", "")
    ok = models.chat_logger.rate_message(reviewer_id, timestamp, rating)
    return jsonify({"ok": ok})


@admin_bp.route("/api/campaign/preview", methods=["POST"])
@admin_required
def api_campaign_preview():
    """캠페인 등록 미리보기 (카드 + 모집글)"""
    data = request.get_json(silent=True) or {}
    from modules.utils import safe_int

    product_name = data.get("상품명", "")
    store_name = data.get("업체명", "")
    total = safe_int(data.get("총수량", 0))
    product_price = data.get("상품금액", "") or "확인필요"
    review_fee = data.get("리뷰비", "") or "미정"
    buy_time = data.get("구매가능시간", "")
    custom_guide = data.get("캠페인가이드", "").strip()

    # 카드 데이터 (chat.js에서 렌더링하는 형식과 동일)
    card = {
        "name": product_name,
        "store": store_name,
        "total": total,
        "remaining": total,
        "daily_target": 0,
        "today_done": 0,
        "urgent": total <= 5,
        "price": product_price,
    }

    # 구매 가이드 텍스트 (리뷰어에게 전달되는 형태)
    guide_parts = [
        "━━━━━━━━━━━━━━━━━━",
        f"📌 {product_name} 구매 가이드",
        "━━━━━━━━━━━━━━━━━━",
        "",
    ]
    if custom_guide:
        guide_parts.append(custom_guide)
    else:
        guide_parts.append("(가이드 미입력)")
    guide_parts.append("")
    if buy_time:
        guide_parts.append(f"⏰ 구매 가능 시간: {buy_time}")
        guide_parts.append("")
    guide_parts.append("✏️ 구매 완료 후 양식을 입력해주세요.")

    # 모집글 텍스트
    campaign_type = data.get("캠페인유형", "실배송") or "실배송"
    recruit_lines = [
        "📢 체험단 모집",
        "",
        product_name,
        f"💰 결제금액: {product_price}원",
        f"📦 {campaign_type}",
        f"👥 {total}명 모집 (남은 {total}자리)",
        "",
        "👉 신청하기",
    ]

    return jsonify({
        "card": card,
        "guide_text": "\n".join(guide_parts),
        "recruit_text": "\n".join(recruit_lines),
    })

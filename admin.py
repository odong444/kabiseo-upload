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


def _generate_schedule(total: int, lo: int, hi: int, days: int) -> list[int]:
    """총수량을 일수에 맞게 lo~hi 범위로 랜덤 배분"""
    import random
    schedule = []
    remaining = total
    for i in range(days):
        if i == days - 1:
            schedule.append(max(0, remaining))
        else:
            left = days - i - 1
            min_today = max(lo, remaining - hi * left)
            max_today = min(hi, remaining - lo * left)
            if min_today > max_today:
                min_today = max_today = max(1, remaining // (left + 1))
            val = random.randint(max(1, min_today), max(1, max_today))
            val = min(val, remaining)
            schedule.append(val)
            remaining -= val
    return schedule

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin1234")


@admin_bp.app_context_processor
def inject_pending_count():
    """모든 admin 페이지에 문의 대기 건수 주입"""
    if session.get("admin_logged_in") and models.db_manager:
        try:
            return {"pending_inquiry_count": models.db_manager.get_pending_inquiry_count()}
        except Exception:
            pass
    return {"pending_inquiry_count": 0}


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
    if models.db_manager:
        stats = models.db_manager.get_today_stats()
    recent_messages = models.chat_logger.get_recent_messages(20)
    return render_template("admin/dashboard.html", stats=stats, recent_messages=recent_messages)


# ──────── 캠페인 관리 ────────

@admin_bp.route("/campaigns")
@admin_required
def campaigns():
    import re as _re
    from datetime import date as _date, datetime as _datetime
    from modules.utils import safe_int

    campaign_list = []
    if models.campaign_manager:
        campaign_list = models.campaign_manager.get_all_campaigns()

    # 실시간 통계 반영
    stats = {}
    if models.db_manager:
        stats = models.db_manager.get_campaign_stats()
    for c in campaign_list:
        cid = c.get("캠페인ID", "")
        s = stats.get(cid, {})
        c["완료수량"] = str(s.get("done", 0))
        c["오늘수량"] = str(s.get("today", 0))
        c["신청수"] = str(s.get("active", 0))
        c["구매완료"] = str(s.get("purchase_done", 0))
        c["리뷰완료"] = str(s.get("review_done", 0))
        c["정산완료"] = str(s.get("settlement_done", 0))

        # 오늘 목표 계산
        today_target = 0
        schedule = c.get("일정", [])
        start_date_str = c.get("시작일", "").strip() if c.get("시작일") else ""
        if schedule and start_date_str:
            try:
                start = _datetime.strptime(start_date_str, "%Y-%m-%d").date()
                day_index = (_date.today() - start).days
                if 0 <= day_index < len(schedule):
                    today_target = safe_int(schedule[day_index])
            except Exception:
                pass
        if not today_target:
            daily_str = c.get("일수량", "").strip() if c.get("일수량") else ""
            if daily_str:
                range_match = _re.match(r"(\d+)\s*[-~]\s*(\d+)", daily_str)
                if range_match:
                    today_target = safe_int(range_match.group(2))
                else:
                    today_target = safe_int(daily_str)
        c["오늘목표"] = str(today_target)

    return render_template("admin/campaigns.html", campaigns=campaign_list)


@admin_bp.route("/campaigns/<campaign_id>/edit", methods=["GET"])
@admin_required
def campaign_edit(campaign_id):
    if not models.db_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.campaigns"))

    campaign = models.db_manager.get_campaign_by_id(campaign_id)
    if not campaign:
        flash("캠페인을 찾을 수 없습니다.")
        return redirect(url_for("admin.campaigns"))

    return render_template("admin/campaign_edit.html", campaign=campaign, row=campaign_id)


@admin_bp.route("/campaigns/<campaign_id>/edit", methods=["POST"])
@admin_required
def campaign_edit_post(campaign_id):
    if not models.db_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.campaigns"))

    editable_fields = [
        "상태", "상품명", "업체명", "플랫폼", "캠페인유형",
        "상품금액", "리뷰비", "결제금액",
        "총수량", "일수량", "진행일수", "완료수량", "일정", "시작일",
        "구매가능시간", "중복허용",
        "상품링크", "키워드", "유입방식", "리뷰기한일수",
        "공개여부", "캠페인가이드", "메모",
    ]

    update_data = {}
    for field_name in editable_fields:
        value = request.form.get(field_name, "").strip()
        update_data[field_name] = value

    # 상품이미지 파일 업로드
    image_file = request.files.get("상품이미지")
    if image_file and image_file.filename:
        try:
            if models.drive_uploader:
                link = models.drive_uploader.upload_from_flask_file(
                    image_file, capture_type="purchase",
                    description=f"캠페인 상품이미지: {update_data.get('상품명', '')}"
                )
                update_data["상품이미지"] = link
        except Exception as e:
            logger.error(f"상품이미지 업로드 에러: {e}")

    # 상품링크에서 상품코드 자동 추출
    from modules.utils import extract_product_codes
    product_link = update_data.get("상품링크", "")
    if product_link:
        codes = extract_product_codes(product_link)
        if codes:
            update_data["상품코드"] = codes

    try:
        logger.info(f"캠페인 수정 시도: {campaign_id}, data: {update_data}")
        models.db_manager.update_campaign(campaign_id, update_data)
        flash("캠페인이 수정되었습니다.")
    except Exception as e:
        logger.error(f"캠페인 수정 에러: {e}", exc_info=True)
        flash(f"수정 중 오류 발생: {e}")
    return redirect(url_for("admin.campaigns"))


# ──────── 캠페인 신규 등록 ────────

@admin_bp.route("/campaigns/new", methods=["GET"])
@admin_required
def campaign_new():
    return render_template("admin/campaign_new.html")


@admin_bp.route("/campaigns/new", methods=["POST"])
@admin_required
def campaign_new_post():
    if not models.db_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.campaigns"))

    import re
    import uuid
    import random
    from modules.utils import today_str, safe_int

    campaign_id = str(uuid.uuid4())[:8]

    fields = [
        "캠페인유형", "플랫폼", "업체명", "상품명",
        "총수량", "일수량", "진행일수",
        "상품금액", "리뷰비", "중복허용", "구매가능시간", "캠페인가이드",
        "상품링크",
    ]

    data = {"캠페인ID": campaign_id, "등록일": today_str(), "상태": "모집중", "완료수량": "0"}
    for field in fields:
        data[field] = request.form.get(field, "").strip()

    # 상품링크에서 상품코드 자동 추출
    from modules.utils import extract_product_codes
    product_link = data.get("상품링크", "")
    if product_link:
        codes = extract_product_codes(product_link)
        if codes:
            data["상품코드"] = codes

    # 일정 자동 생성
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
            schedule = _generate_schedule(total, lo, hi, days)
            data["일정"] = schedule
            data["시작일"] = today_str()

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
    if models.db_manager:
        all_items = models.db_manager.get_all_reviewers()
        items = [i for i in all_items if i.get("상태") == "리뷰제출"]
    items = _sort_by_date_asc(items)
    return render_template("admin/reviews.html", items=items)


@admin_bp.route("/reviews/approve", methods=["POST"])
@admin_required
def reviews_approve():
    if not models.db_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.reviews"))

    row_indices = request.form.getlist("row_idx")
    processed = 0
    for id_str in row_indices:
        try:
            progress_id = int(id_str)
            models.db_manager.approve_review(progress_id)
            processed += 1
        except Exception as e:
            logger.error(f"검수 승인 에러 (id {id_str}): {e}")

    flash(f"{processed}건 승인 완료 (입금대기)")
    return redirect(url_for("admin.reviews"))


@admin_bp.route("/reviews/reject", methods=["POST"])
@admin_required
def reviews_reject():
    if not models.db_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.reviews"))

    row_indices = request.form.getlist("row_idx")
    reason = request.form.get("reason", "").strip() or "리뷰 사진을 다시 확인해주세요."
    processed = 0

    for id_str in row_indices:
        try:
            progress_id = int(id_str)
            row_data = models.db_manager.get_row_dict(progress_id)
            models.db_manager.reject_review(progress_id, reason)
            processed += 1
            _notify_reviewer_reject(row_data, reason)
            # 카카오톡 반려 알림
            if models.kakao_notifier:
                models.kakao_notifier.notify_review_rejected(progress_id, reason)
        except Exception as e:
            logger.error(f"검수 반려 에러 (id {id_str}): {e}")

    flash(f"{processed}건 반려 완료")
    return redirect(url_for("admin.reviews"))


# 검수 AJAX API (모달에서 사용)
@admin_bp.route("/api/reviews/approve", methods=["POST"])
@admin_required
def api_reviews_approve():
    if not models.db_manager:
        return jsonify({"ok": False, "message": "시스템 초기화 중"})
    data = request.get_json(silent=True) or {}
    row_idx = data.get("row_idx")
    try:
        models.db_manager.approve_review(int(row_idx))
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"검수 승인 API 에러: {e}")
        return jsonify({"ok": False, "message": str(e)})


@admin_bp.route("/api/reviews/reject", methods=["POST"])
@admin_required
def api_reviews_reject():
    if not models.db_manager:
        return jsonify({"ok": False, "message": "시스템 초기화 중"})
    data = request.get_json(silent=True) or {}
    row_idx = data.get("row_idx")
    reason = data.get("reason", "").strip() or "리뷰 사진을 다시 확인해주세요."
    try:
        progress_id = int(row_idx)
        row_data = models.db_manager.get_row_dict(progress_id)
        models.db_manager.reject_review(progress_id, reason)
        _notify_reviewer_reject(row_data, reason)
        # 카카오톡 반려 알림
        if models.kakao_notifier:
            models.kakao_notifier.notify_review_rejected(progress_id, reason)
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"검수 반려 API 에러: {e}")
        return jsonify({"ok": False, "message": str(e)})


# ──────── 정산 관리 ────────

@admin_bp.route("/settlement")
@admin_required
def settlement():
    items = []
    if models.db_manager:
        all_items = models.db_manager.get_all_reviewers()
        items = [i for i in all_items if i.get("상태") == "입금대기"]
    # 리뷰제출일 오름차순 (오래된 것 먼저)
    items = _sort_by_date_asc(items, "리뷰제출일")
    return render_template("admin/settlement.html", items=items)


@admin_bp.route("/settlement/process", methods=["POST"])
@admin_required
def settlement_process():
    if not models.db_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.settlement"))

    row_indices = request.form.getlist("row_idx")
    processed = 0
    for id_str in row_indices:
        try:
            progress_id = int(id_str)
            row_data = models.db_manager.get_row_dict(progress_id)
            amount = row_data.get("입금금액", "0") or "0"
            models.db_manager.process_settlement(progress_id, amount)
            processed += 1
        except Exception as e:
            logger.error(f"정산 처리 에러 (id {id_str}): {e}")

    flash(f"{processed}건 정산 처리 완료")
    return redirect(url_for("admin.settlement"))


@admin_bp.route("/settlement/download")
@admin_required
def settlement_download():
    """입금대기 목록 엑셀(CSV) 다운로드"""
    items = []
    if models.db_manager:
        all_items = models.db_manager.get_all_reviewers()
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
    if models.db_manager:
        items = models.db_manager.get_all_reviewers()
    q = request.args.get("q", "").strip()
    if q:
        ql = q.lower()
        items = [
            i for i in items
            if ql in i.get("진행자이름", "").lower()
            or ql in i.get("진행자연락처", "")
            or ql in i.get("수취인명", "").lower()
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
    if not models.db_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.reviewers"))

    row_indices = request.form.getlist("row_idx")
    processed = 0
    for id_str in row_indices:
        try:
            progress_id = int(id_str)
            models.db_manager.restore_from_timeout(progress_id)
            processed += 1
        except Exception as e:
            logger.error(f"타임아웃 복원 에러 (id {id_str}): {e}")

    flash(f"{processed}건 가이드전달 상태로 복원 완료")
    return redirect(url_for("admin.reviewers"))


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

    # 카드 데이터
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

    # 구매 가이드 텍스트
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


# ──────── 카카오톡 수동 발송 ────────

@admin_bp.route("/api/kakao/send", methods=["POST"])
@admin_required
def api_kakao_send():
    """카카오톡 단건 발송"""
    if not models.kakao_notifier:
        return jsonify({"ok": False, "message": "카카오 알림 미설정"})

    data = request.get_json(silent=True) or {}
    progress_id = data.get("progress_id")
    custom_message = data.get("message", "").strip()

    if not progress_id:
        return jsonify({"ok": False, "message": "progress_id 필수"})

    try:
        ok = models.kakao_notifier.send_reminder(int(progress_id), custom_message)
        return jsonify({"ok": ok, "message": "발송 성공" if ok else "발송 실패"})
    except Exception as e:
        logger.error(f"카톡 발송 에러: {e}")
        return jsonify({"ok": False, "message": str(e)})


@admin_bp.route("/api/kakao/bulk", methods=["POST"])
@admin_required
def api_kakao_bulk():
    """카카오톡 일괄 발송"""
    if not models.kakao_notifier:
        return jsonify({"ok": False, "message": "카카오 알림 미설정"})

    data = request.get_json(silent=True) or {}
    progress_ids = data.get("progress_ids", [])
    custom_message = data.get("message", "").strip()

    if not progress_ids:
        return jsonify({"ok": False, "message": "progress_ids 필수"})

    sent = 0
    for pid in progress_ids:
        try:
            ok = models.kakao_notifier.send_reminder(int(pid), custom_message)
            if ok:
                sent += 1
        except Exception:
            pass

    return jsonify({"ok": True, "sent": sent, "total": len(progress_ids)})


# ──────── 친구추가 재시도 ────────

@admin_bp.route("/api/friend-add", methods=["POST"])
@admin_required
def api_friend_add():
    """서버PC에 카카오톡 친구추가 재시도 요청"""
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    phone = data.get("phone", "").strip()

    if not name or not phone:
        return jsonify({"ok": False, "error": "name, phone 필수"})

    from modules.signal_sender import request_friend_add
    ok = request_friend_add(name, phone)
    if ok:
        return jsonify({"ok": True})
    else:
        return jsonify({"ok": False, "error": "서버PC 연결 실패 또는 태스크 전송 거부"})


# ──────── 문의 관리 ────────

@admin_bp.route("/inquiries")
@admin_required
def inquiries():
    status_filter = request.args.get("status", "")
    items = []
    if models.db_manager:
        items = models.db_manager.get_inquiries(status_filter or None)
    return render_template("admin/inquiries.html",
                           inquiries=items, status_filter=status_filter)


@admin_bp.route("/api/inquiry/reply", methods=["POST"])
@admin_required
def api_inquiry_reply():
    """문의 답변 → DB 업데이트 + 카톡 발송"""
    data = request.get_json(silent=True) or {}
    inquiry_id = data.get("inquiry_id")
    reply_text = data.get("reply", "").strip()

    if not inquiry_id or not reply_text:
        return jsonify({"ok": False, "message": "inquiry_id, reply 필수"})

    if not models.db_manager:
        return jsonify({"ok": False, "message": "DB 미설정"})

    # 문의 정보 조회
    inquiry = models.db_manager.get_inquiry(int(inquiry_id))
    if not inquiry:
        return jsonify({"ok": False, "message": "문의를 찾을 수 없습니다"})

    # DB 업데이트
    ok = models.db_manager.reply_inquiry(int(inquiry_id), reply_text)
    if not ok:
        return jsonify({"ok": False, "message": "답변 저장 실패"})

    reviewer_name = inquiry.get("reviewer_name", "")
    reviewer_phone = inquiry.get("reviewer_phone", "")
    is_urgent = inquiry.get("is_urgent", False)

    # 1) 웹 채팅에 답변 메시지 전송 (일반/긴급 모두)
    chat_msg = f"[문의 답변]\n{reply_text}\n\n※ 추가 문의는 메뉴에서 '담당자 문의'를 이용해주세요."
    if reviewer_name and reviewer_phone:
        rid = f"{reviewer_name}_{reviewer_phone}"
        if models.chat_logger:
            models.chat_logger.log(rid, "bot", chat_msg)
        if models.timeout_manager and models.timeout_manager._socketio:
            models.timeout_manager._socketio.emit(
                "bot_message", {"message": chat_msg}, room=rid
            )

    # 2) 긴급문의만 카톡으로 추가 발송
    kakao_ok = False
    if is_urgent and models.kakao_notifier and reviewer_name and reviewer_phone:
        try:
            kakao_ok = models.kakao_notifier.notify_inquiry_reply(
                reviewer_name, reviewer_phone, reply_text
            )
        except Exception as e:
            logger.error(f"문의 답변 카톡 발송 실패: {e}")

    msg = "답변 완료 (웹채팅 전송됨)"
    if is_urgent:
        msg += " + 카톡 발송" + ("됨" if kakao_ok else " 실패")
    return jsonify({"ok": True, "message": msg})


# ──────── 디버그 ────────

@admin_bp.route("/api/debug/campaigns")
@admin_required
def debug_campaigns():
    """캠페인 데이터 디버그용"""
    result = {"campaigns": [], "active": [], "cards": [], "stats": {}}
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
    if models.db_manager:
        result["stats"] = models.db_manager.get_campaign_stats()
        try:
            result["count_all"] = models.db_manager.count_all_campaigns()
        except Exception as e:
            result["count_all_error"] = str(e)
    return jsonify(result)


# ──────── 스프레드시트 (데이터 편집) ────────

@admin_bp.route("/spreadsheet")
@admin_required
def spreadsheet():
    items = []
    campaigns = []
    if models.db_manager:
        items = models.db_manager.get_all_reviewers()
        try:
            campaigns = models.campaign_manager.get_all() if models.campaign_manager else []
        except Exception:
            campaigns = []
    campaign_filter = request.args.get("campaign", "")
    status_filter = request.args.get("status", "")
    if campaign_filter:
        items = [i for i in items if i.get("캠페인ID") == campaign_filter]
    if status_filter:
        items = [i for i in items if i.get("상태") == status_filter]
    return render_template("admin/spreadsheet.html",
                           items=items, campaigns=campaigns,
                           campaign_filter=campaign_filter,
                           status_filter=status_filter)


@admin_bp.route("/api/progress/update", methods=["POST"])
@admin_required
def api_progress_update():
    """셀 단위 수정"""
    if not models.db_manager:
        return jsonify({"ok": False, "message": "DB 미설정"})

    data = request.get_json(silent=True) or {}
    progress_id = data.get("progress_id")
    field = data.get("field", "")
    value = data.get("value", "")

    if not progress_id or not field:
        return jsonify({"ok": False, "message": "progress_id, field 필수"})

    try:
        models.db_manager.update_progress_field(int(progress_id), field, value)
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"스프레드시트 수정 에러: {e}")
        return jsonify({"ok": False, "message": str(e)})


# ──────── 설정 (담당자 관리) ────────

@admin_bp.route("/settings")
@admin_required
def settings():
    """담당자 설정 페이지"""
    managers = []
    if models.db_manager:
        managers = models.db_manager.get_managers()
    return render_template("admin/settings.html", managers=managers)


@admin_bp.route("/api/managers", methods=["GET"])
@admin_required
def api_managers_list():
    """담당자 목록 JSON"""
    if not models.db_manager:
        return jsonify({"ok": False, "error": "DB 미설정"})
    managers = models.db_manager.get_managers()
    return jsonify({"ok": True, "managers": managers})


@admin_bp.route("/api/managers", methods=["POST"])
@admin_required
def api_managers_add():
    """담당자 추가"""
    if not models.db_manager:
        return jsonify({"ok": False, "error": "DB 미설정"})

    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    phone = data.get("phone", "").strip()
    role = data.get("role", "담당자").strip()

    if not name or not phone:
        return jsonify({"ok": False, "error": "이름, 연락처 필수"})

    mid = models.db_manager.add_manager(name, phone, role)
    if not mid:
        return jsonify({"ok": False, "error": "이미 등록된 담당자"})

    # 발송시간 설정
    notify_start = data.get("notify_start", "").strip()
    notify_end = data.get("notify_end", "").strip()
    if notify_start or notify_end:
        kwargs = {}
        if notify_start:
            kwargs["notify_start"] = notify_start
        if notify_end:
            kwargs["notify_end"] = notify_end
        models.db_manager.update_manager(mid, **kwargs)

    return jsonify({"ok": True, "id": mid})


@admin_bp.route("/api/managers/<int:mid>", methods=["PUT"])
@admin_required
def api_managers_update(mid):
    """담당자 수정"""
    if not models.db_manager:
        return jsonify({"ok": False, "error": "DB 미설정"})

    data = request.get_json(silent=True) or {}
    kwargs = {}
    if "name" in data:
        kwargs["name"] = data["name"].strip()
    if "phone" in data:
        kwargs["phone"] = data["phone"].strip()
    if "role" in data:
        kwargs["role"] = data["role"].strip()
    if "receive_kakao" in data:
        kwargs["receive_kakao"] = bool(data["receive_kakao"])

    models.db_manager.update_manager(mid, **kwargs)
    return jsonify({"ok": True})


@admin_bp.route("/api/managers/<int:mid>", methods=["DELETE"])
@admin_required
def api_managers_delete(mid):
    """담당자 삭제"""
    if not models.db_manager:
        return jsonify({"ok": False, "error": "DB 미설정"})

    models.db_manager.delete_manager(mid)
    return jsonify({"ok": True})


@admin_bp.route("/api/progress/delete", methods=["POST"])
@admin_required
def api_progress_delete():
    """행 삭제"""
    if not models.db_manager:
        return jsonify({"ok": False, "message": "DB 미설정"})

    data = request.get_json(silent=True) or {}
    progress_id = data.get("progress_id")
    if not progress_id:
        return jsonify({"ok": False, "message": "progress_id 필수"})

    try:
        ok = models.db_manager.delete_progress(int(progress_id))
        return jsonify({"ok": ok})
    except Exception as e:
        logger.error(f"행 삭제 에러: {e}")
        return jsonify({"ok": False, "message": str(e)})

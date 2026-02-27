"""
admin.py - 관리자 대시보드 라우트
"""

import os
import io
import csv
import logging
import requests as _requests
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

SERVER_PC_URL = "http://222.122.194.202:5050"
SERVER_PC_API_KEY = "_fNmY5SeHyigMgkR5LIngpxBB1gDoZLF"


def _fetch_server_categories() -> list[str]:
    """서버PC에서 채팅방 카테고리 목록 가져오기"""
    try:
        resp = _requests.get(
            f"{SERVER_PC_URL}/api/categories",
            headers={"X-API-Key": SERVER_PC_API_KEY},
            timeout=3,
        )
        if resp.ok:
            return resp.json().get("categories", [])
    except Exception:
        pass
    return ["체험단", "리뷰-실", "리뷰-빈", "마케팅홍보"]  # fallback


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
    recent_activities = []
    if models.db_manager:
        stats = models.db_manager.get_today_stats()
        recent_activities = models.db_manager.get_recent_activities(30)
    return render_template("admin/dashboard.html", stats=stats, recent_activities=recent_activities)


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

        # 당일마감 판단
        today_done = safe_int(c.get("오늘수량", 0))
        if today_target > 0 and today_done >= today_target and c.get("상태") in ("모집중", "진행중", ""):
            c["_daily_closed"] = True

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

    categories = _fetch_server_categories()
    return render_template("admin/campaign_edit.html", campaign=campaign, row=campaign_id, promo_category_list=categories)


@admin_bp.route("/campaigns/<campaign_id>/edit", methods=["POST"])
@admin_required
def campaign_edit_post(campaign_id):
    if not models.db_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.campaigns"))

    editable_fields = [
        "상태", "캠페인명", "상품명", "업체명", "플랫폼", "캠페인유형",
        "상품금액", "리뷰비", "결제금액",
        "총수량", "일수량", "진행일수", "1인일일제한", "일정", "시작일",
        "구매가능시간", "중복허용",
        "상품링크", "키워드", "유입방식", "리뷰기한일수",
        "공개여부", "캠페인가이드", "메모", "홍보메시지",
        "홍보활성", "홍보카테고리", "홍보시작시간", "홍보종료시간", "홍보주기",
        "AI구매검수지침", "AI리뷰검수지침",
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


# ──────── 캠페인 중지/재개/삭제 API ────────

@admin_bp.route("/api/campaigns/<campaign_id>/pause", methods=["POST"])
@admin_required
def api_campaign_pause(campaign_id):
    """캠페인 상태를 '중지'로 변경 + 서버PC 대기 홍보 태스크 취소"""
    if not models.db_manager:
        return jsonify({"ok": False, "message": "시스템 초기화 중"})
    try:
        models.db_manager.update_campaign_status(campaign_id, "중지")
        # 서버PC의 대기 중 홍보 태스크 취소
        cancelled = 0
        try:
            import requests
            resp = requests.post(
                f"{SERVER_PC_URL}/api/task/cancel-campaign",
                json={"campaign_id": campaign_id},
                headers={"X-API-Key": SERVER_PC_API_KEY},
                timeout=5,
            )
            if resp.ok:
                cancelled = resp.json().get("cancelled", 0)
        except Exception as ce:
            logger.warning(f"서버PC 태스크 취소 요청 실패: {ce}")
        return jsonify({"ok": True, "status": "중지", "cancelled_tasks": cancelled})
    except Exception as e:
        logger.error(f"캠페인 중지 에러: {e}")
        return jsonify({"ok": False, "message": str(e)})


@admin_bp.route("/api/campaigns/<campaign_id>/resume", methods=["POST"])
@admin_required
def api_campaign_resume(campaign_id):
    """캠페인 상태를 '모집중'으로 복원"""
    if not models.db_manager:
        return jsonify({"ok": False, "message": "시스템 초기화 중"})
    try:
        models.db_manager.update_campaign_status(campaign_id, "모집중")
        return jsonify({"ok": True, "status": "모집중"})
    except Exception as e:
        logger.error(f"캠페인 재개 에러: {e}")
        return jsonify({"ok": False, "message": str(e)})


@admin_bp.route("/api/campaigns/<campaign_id>", methods=["DELETE"])
@admin_required
def api_campaign_delete(campaign_id):
    """캠페인 삭제 (연결된 progress도 함께 삭제)"""
    if not models.db_manager:
        return jsonify({"ok": False, "message": "시스템 초기화 중"})
    try:
        ok = models.db_manager.delete_campaign(campaign_id)
        if ok:
            return jsonify({"ok": True})
        else:
            return jsonify({"ok": False, "message": "캠페인을 찾을 수 없습니다"})
    except Exception as e:
        logger.error(f"캠페인 삭제 에러: {e}")
        return jsonify({"ok": False, "message": str(e)})


# ──────── 캠페인 신규 등록 ────────

@admin_bp.route("/campaigns/new", methods=["GET"])
@admin_required
def campaign_new():
    categories = _fetch_server_categories()
    return render_template("admin/campaign_new.html", promo_category_list=categories)


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
        "캠페인유형", "플랫폼", "업체명", "캠페인명", "상품명",
        "총수량", "일수량", "진행일수", "1인일일제한",
        "상품금액", "리뷰비", "중복허용", "구매가능시간", "캠페인가이드",
        "상품링크", "홍보메시지",
        "홍보활성", "홍보카테고리", "홍보시작시간", "홍보종료시간", "홍보주기",
        "AI구매검수지침", "AI리뷰검수지침",
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
        display_name = data.get('캠페인명', '').strip() or data['상품명']
        flash(f"캠페인 '{display_name}' 등록 완료 (ID: {campaign_id})")
    except Exception as e:
        logger.error(f"캠페인 등록 에러: {e}")
        flash(f"등록 중 오류가 발생했습니다: {e}")

    return redirect(url_for("admin.campaigns"))


# ──────── 대화 이력 ────────

@admin_bp.route("/chat/<reviewer_id>")
@admin_required
def chat_viewer(reviewer_id):
    history = models.chat_logger.get_history(reviewer_id)
    reviewer_ids = models.chat_logger.get_all_reviewer_ids()
    q = request.args.get("q", "").strip()
    if q:
        reviewer_ids = [r for r in reviewer_ids if q.lower() in r.lower()]
    return render_template("admin/chat_viewer.html", reviewer_id=reviewer_id, history=history, reviewer_ids=reviewer_ids, q=q)


@admin_bp.route("/chat")
@admin_required
def chat_list():
    reviewer_ids = models.chat_logger.get_all_reviewer_ids()
    return render_template("admin/chat_viewer.html", reviewer_ids=reviewer_ids, reviewer_id=None, history=[], q="")


@admin_bp.route("/api/chat/search")
@admin_required
def api_chat_search():
    """대화이력 실시간 검색 API"""
    q = request.args.get("q", "").strip()
    if not q or len(q) < 1:
        # 검색어 없으면 전체 리뷰어 목록
        reviewer_ids = models.chat_logger.get_all_reviewer_ids()
        return jsonify({"reviewer_ids": reviewer_ids, "messages": []})

    # 리뷰어 ID 필터
    all_ids = models.chat_logger.get_all_reviewer_ids()
    matched_ids = [r for r in all_ids if q.lower() in r.lower()]

    # 메시지 내용 검색
    messages = models.chat_logger.search(q)[:50]

    # 메시지에서 발견된 리뷰어 ID도 추가
    msg_rids = list(dict.fromkeys(m["reviewer_id"] for m in messages))
    combined_ids = list(dict.fromkeys(matched_ids + msg_rids))

    return jsonify({"reviewer_ids": combined_ids, "messages": messages})


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


# ──────── AI 검수 API ────────

@admin_bp.route("/api/ai-verify/override", methods=["POST"])
@admin_required
def api_ai_override():
    """관리자가 AI 검수 결과를 오버라이드"""
    if not models.db_manager:
        return jsonify({"ok": False, "message": "시스템 초기화 중"})
    data = request.get_json(silent=True) or {}
    progress_id = data.get("progress_id")
    override = data.get("override", "")  # "통과" or "반려"
    try:
        models.db_manager._execute(
            "UPDATE progress SET ai_override = %s WHERE id = %s",
            (override, int(progress_id)),
        )
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"AI 오버라이드 에러: {e}")
        return jsonify({"ok": False, "message": str(e)})


def _build_ai_instructions(campaign_id: str, capture_type: str) -> str:
    """글로벌 AI 지침 + 캠페인별 타입별 AI 지침을 결합"""
    parts = []
    if models.db_manager:
        # 글로벌 지침
        global_key = "ai_global_purchase" if capture_type == "purchase" else "ai_global_review"
        global_instr = models.db_manager.get_setting(global_key, "")
        if global_instr:
            parts.append(global_instr)
        # 캠페인별 지침
        if campaign_id:
            campaign = models.db_manager.get_campaign_by_id(campaign_id)
            if campaign:
                field = "AI구매검수지침" if capture_type == "purchase" else "AI리뷰검수지침"
                camp_instr = campaign.get(field, "") or campaign.get("AI검수지침", "")
                if camp_instr:
                    parts.append(camp_instr)
    return "\n".join(parts)


@admin_bp.route("/api/ai-verify/recheck", methods=["POST"])
@admin_required
def api_ai_recheck():
    """AI 검수 재실행 (단건)"""
    if not models.db_manager:
        return jsonify({"ok": False, "message": "시스템 초기화 중"})
    data = request.get_json(silent=True) or {}
    progress_id = data.get("progress_id")
    capture_type = data.get("capture_type", "purchase")
    try:
        row = models.db_manager.get_row_dict(int(progress_id))
        url_key = "구매캡쳐링크" if capture_type == "purchase" else "리뷰캡쳐링크"
        drive_url = row.get(url_key, "")
        if not drive_url:
            return jsonify({"ok": False, "message": "캡쳐 URL 없음"})

        from modules.capture_verifier import verify_capture_async
        ai_instructions = _build_ai_instructions(row.get("캠페인ID", ""), capture_type)
        verify_capture_async(drive_url, capture_type, int(progress_id), models.db_manager, ai_instructions)
        return jsonify({"ok": True, "message": "AI 검수 재실행 중"})
    except Exception as e:
        logger.error(f"AI 재검수 에러: {e}")
        return jsonify({"ok": False, "message": str(e)})


@admin_bp.route("/api/ai-verify/batch", methods=["POST"])
@admin_required
def api_ai_batch():
    """기존 건 AI 검수 일괄 실행 (캡쳐 URL이 있고 AI 결과가 없는 건)"""
    if not models.db_manager:
        return jsonify({"ok": False, "message": "시스템 초기화 중"})
    data = request.get_json(silent=True) or {}
    limit = int(data.get("limit", 50))

    try:
        from modules.capture_verifier import verify_capture_async

        # 구매캡쳐 URL이 있지만 AI 검수 안 된 건
        rows = models.db_manager._fetchall("""
            SELECT id, campaign_id, purchase_capture_url, review_capture_url
            FROM progress
            WHERE (
                (purchase_capture_url != '' AND purchase_capture_url IS NOT NULL AND (ai_purchase_result = '' OR ai_purchase_result IS NULL))
                OR
                (review_capture_url != '' AND review_capture_url IS NOT NULL AND (ai_review_result = '' OR ai_review_result IS NULL))
            )
            ORDER BY created_at DESC
            LIMIT %s
        """, (limit,))

        triggered = 0
        for row in rows:
            progress_id = row["id"]
            campaign_id = row.get("campaign_id", "")

            if row.get("purchase_capture_url"):
                ai_instr = _build_ai_instructions(campaign_id, "purchase")
                verify_capture_async(row["purchase_capture_url"], "purchase", progress_id, models.db_manager, ai_instr)
                triggered += 1

            if row.get("review_capture_url"):
                ai_instr = _build_ai_instructions(campaign_id, "review")
                verify_capture_async(row["review_capture_url"], "review", progress_id, models.db_manager, ai_instr)
                triggered += 1

        return jsonify({"ok": True, "message": f"{triggered}건 AI 검수 시작", "count": triggered})
    except Exception as e:
        logger.error(f"AI 일괄검수 에러: {e}")
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

    campaign_name = data.get("캠페인명", "").strip()
    product_name = campaign_name or data.get("상품명", "")
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
            {k: v for k, v in c.items() if k in ("캠페인ID", "캠페인명", "상품명", "상태", "총수량", "완료수량", "일수량", "공개여부", "구매가능시간")}
            for c in all_c
        ]
        active = models.campaign_manager.get_active_campaigns()
        result["active"] = [
            {k: v for k, v in c.items() if k in ("캠페인ID", "캠페인명", "상품명", "상태", "총수량", "_남은수량", "_buy_time_active")}
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
            campaigns = models.campaign_manager.get_all_campaigns() if models.campaign_manager else []
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


@admin_bp.route("/api/timeout-sessions")
@admin_required
def api_timeout_sessions():
    """가이드전달 후 양식 미제출 타임아웃 대기 세션 목록 (이중 타임아웃)"""
    import time as _time
    from datetime import timezone as _tz
    sessions = []
    timeout_sec = models.timeout_manager.timeout if models.timeout_manager else 1200

    # 1) 인메모리 세션 기반
    seen_keys = set()
    if models.state_store and models.timeout_manager:
        for state in models.state_store.all_states():
            if state.step not in (4, 5):
                continue
            submitted = state.temp_data.get("submitted_ids", [])
            store_ids = state.temp_data.get("store_ids", [])
            if store_ids and set(submitted) >= set(store_ids):
                continue
            # 이중 타임아웃: DB created_at vs last_activity 중 더 나중
            db_created = models.timeout_manager._get_db_created_epoch(state)
            baseline = max(state.last_activity, db_created) if db_created else state.last_activity
            elapsed = _time.time() - baseline
            remaining = max(0, int(timeout_sec - elapsed))
            if remaining <= 0:
                continue
            campaign = state.temp_data.get("campaign", {})
            product = campaign.get("캠페인명", "") or campaign.get("상품명", "")
            pending = [s for s in store_ids if s not in submitted]
            sessions.append({
                "name": state.name,
                "phone": state.phone,
                "remaining_sec": remaining,
                "product": product,
                "store_ids": ", ".join(pending),
            })
            seen_keys.add((state.name, state.phone))

    # 2) DB 기반 (인메모리 세션 없는 건 = 서버 재시작 후)
    if models.db_manager:
        try:
            rows = models.db_manager._fetchall(
                """SELECT p.created_at, r.name, r.phone, p.store_id,
                          COALESCE(NULLIF(c.campaign_name,''), c.product_name) as product
                   FROM progress p
                   LEFT JOIN reviewers r ON p.reviewer_id = r.id
                   LEFT JOIN campaigns c ON p.campaign_id = c.id
                   WHERE p.status = '가이드전달'"""
            )
            for r in rows:
                key = (r["name"], r["phone"])
                if key in seen_keys:
                    continue
                dt = r["created_at"]
                if dt:
                    epoch = dt.replace(tzinfo=_tz.utc).timestamp() if dt.tzinfo is None else dt.timestamp()
                    elapsed = _time.time() - epoch
                    remaining = max(0, int(timeout_sec - elapsed))
                    if remaining <= 0:
                        continue
                    sessions.append({
                        "name": r["name"] or "",
                        "phone": r["phone"] or "",
                        "remaining_sec": remaining,
                        "product": r["product"] or "",
                        "store_ids": r["store_id"] or "",
                    })
                    seen_keys.add(key)
        except Exception:
            pass

    sessions.sort(key=lambda x: x["remaining_sec"])
    return jsonify({"sessions": sessions})


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
    suppliers = []
    ai_settings = {}
    if models.db_manager:
        managers = models.db_manager.get_managers()
        suppliers = models.db_manager.get_suppliers()
        ai_settings = {
            "ai_global_purchase": models.db_manager.get_setting("ai_global_purchase", ""),
            "ai_global_review": models.db_manager.get_setting("ai_global_review", ""),
        }
    return render_template("admin/settings.html", managers=managers, suppliers=suppliers, ai_settings=ai_settings)


@admin_bp.route("/api/settings/ai", methods=["POST"])
@admin_required
def api_settings_ai():
    """AI 기본 검수지침 저장"""
    if not models.db_manager:
        return jsonify({"ok": False, "error": "시스템 초기화 중"})
    data = request.get_json(silent=True) or {}
    models.db_manager.set_setting("ai_global_purchase", data.get("ai_global_purchase", ""))
    models.db_manager.set_setting("ai_global_review", data.get("ai_global_review", ""))
    return jsonify({"ok": True})


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


@admin_bp.route("/api/managers/test", methods=["POST"])
@admin_required
def api_managers_test():
    """담당자에게 테스트 카톡 발송"""
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    phone = data.get("phone", "").strip()
    if not name or not phone:
        return jsonify({"ok": False, "error": "name, phone 필수"})

    from modules.signal_sender import request_notification
    ok = request_notification(name, phone, "[카비서] 테스트 알림입니다.")
    if ok:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "서버PC 연결 실패"})


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


# ═══════════════════════════════════════════
#  공급자 프리셋 (Suppliers)
# ═══════════════════════════════════════════

@admin_bp.route("/api/suppliers", methods=["GET"])
@admin_required
def api_suppliers_list():
    if not models.db_manager:
        return jsonify({"ok": False})
    return jsonify({"ok": True, "suppliers": models.db_manager.get_suppliers()})


@admin_bp.route("/api/suppliers", methods=["POST"])
@admin_required
def api_suppliers_create():
    if not models.db_manager:
        return jsonify({"ok": False, "message": "DB 미설정"})
    data = request.get_json(silent=True) or {}
    sid = models.db_manager.create_supplier(data)
    return jsonify({"ok": True, "id": sid})


@admin_bp.route("/api/suppliers/<int:sid>", methods=["PUT"])
@admin_required
def api_suppliers_update(sid):
    if not models.db_manager:
        return jsonify({"ok": False, "message": "DB 미설정"})
    data = request.get_json(silent=True) or {}
    models.db_manager.update_supplier(sid, data)
    return jsonify({"ok": True})


@admin_bp.route("/api/suppliers/<int:sid>", methods=["DELETE"])
@admin_required
def api_suppliers_delete(sid):
    if not models.db_manager:
        return jsonify({"ok": False, "message": "DB 미설정"})
    models.db_manager.delete_supplier(sid)
    return jsonify({"ok": True})


@admin_bp.route("/api/suppliers/<int:sid>/default", methods=["POST"])
@admin_required
def api_suppliers_set_default(sid):
    if not models.db_manager:
        return jsonify({"ok": False, "message": "DB 미설정"})
    models.db_manager.set_default_supplier(sid)
    return jsonify({"ok": True})


# ═══════════════════════════════════════════
#  견적서 (Quotes)
# ═══════════════════════════════════════════

QUOTE_PARSE_PROMPT = """아래 요청서 텍스트에서 캠페인 등록 + 견적서 작성에 필요한 정보를 JSON으로 추출해줘.
없는 항목은 빈 문자열(""), 불확실하면 빈 문자열로 둬.
JSON만 출력해. 설명이나 코드블록 없이 순수 JSON만.

[추출 필드]
{{
  "campaign": {{
    "상품링크": "",
    "플랫폼": "(스마트스토어/쿠팡/오늘의집/11번가/지마켓/올리브영/기타)",
    "업체명": "",
    "상품명": "",
    "캠페인유형": "(실배송/빈박스)",
    "총수량": "",
    "일수량": "",
    "진행일수": "",
    "상품금액": "(숫자만, 쉼표없이)",
    "리뷰비": "(숫자만)",
    "옵션": "(쉼표 구분)",
    "유입방식": "(링크유입/키워드유입)",
    "키워드": "",
    "키워드위치": "(예: 1페이지 8위)",
    "당일발송": "(Y/N)",
    "발송마감": "(예: 오후 6시 30분)",
    "택배사": "",
    "3PL사용": "(Y/N)",
    "3PL비용": "(숫자만, 건당 비용)",
    "주말작업": "(Y/N)",
    "리뷰제공": "(자체작성/텍스트제공/사진제공)",
    "리뷰원고수": "",
    "중복허용": "(Y/N)",
    "구매가능시간": "",
    "메모": "(기타 특이사항)"
  }},
  "quote": {{
    "recipient": "(업체명)",
    "items": [
      {{"품목": "구매비", "규격": "(상품명 또는 옵션)", "수량": "(숫자)", "단가": "(상품금액 숫자)"}},
      {{"품목": "작업비", "규격": "", "수량": "(총수량)", "단가": "(리뷰비 또는 작업비 숫자)"}}
    ],
    "notes": "1. 구매평 진행 시, 구매 진행 후 취소하시더라도 취소가 어려운 점 참고 부탁드립니다\\n2. 구매가 진행되지 않은 건수에 한해서는 전액 환불이 가능합니다\\n3. 포토리뷰 시 포토와 리뷰가이드는 미리 준비하시면 원활한 진행이 가능합니다\\n4. 택배 분실 시 재발송을 해주셔야 하며, 택배대행시에는 저희가 무상으로 재발송 합니다\\n5. 리뷰는 배송완료일로부터 7일이내 작성되지만, 개인작업자들이다 보니 조금 더 늦을 수 있음을 양해 부탁드립니다"
  }}
}}

규칙:
- 동일 상품이지만 수량/단가가 다른 경우(예: 8건 290,000 + 6건 400,000) 별도 행으로 분리
- 3PL 사용 시 items에 배송대행비 행 추가
- 수량과 단가는 숫자만(쉼표 없이)

[요청서]
{raw_text}

JSON:"""


@admin_bp.route("/quotes")
@admin_required
def quotes():
    if not models.db_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.dashboard"))
    status_filter = request.args.get("status", "")
    items = models.db_manager.get_quotes(status=status_filter or None)
    return render_template("admin/quotes.html", items=items, current_status=status_filter)


@admin_bp.route("/quotes/new", methods=["GET"])
@admin_required
def quote_new():
    suppliers = models.db_manager.get_suppliers() if models.db_manager else []
    return render_template("admin/quote_new.html", suppliers=suppliers)


@admin_bp.route("/quotes/new", methods=["POST"])
@admin_required
def quote_new_post():
    if not models.db_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.quotes"))

    import json
    raw_text = request.form.get("raw_text", "").strip()
    try:
        parsed_data = json.loads(request.form.get("parsed_data", "{}"))
    except Exception:
        parsed_data = {}
    try:
        items = json.loads(request.form.get("items", "[]"))
    except Exception:
        items = []

    supplier_id = request.form.get("supplier_id", "") or None
    if supplier_id:
        supplier_id = int(supplier_id)
    recipient = request.form.get("recipient", "").strip()
    notes = request.form.get("notes", "").strip()

    quote_id = models.db_manager.create_quote(
        raw_text, parsed_data, status="확인대기",
        supplier_id=supplier_id, recipient=recipient, items=items, notes=notes,
    )
    flash(f"견적서 #{quote_id} 저장 완료")
    return redirect(url_for("admin.quote_edit", quote_id=quote_id))


@admin_bp.route("/quotes/<int:quote_id>/edit", methods=["GET"])
@admin_required
def quote_edit(quote_id):
    if not models.db_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.quotes"))
    quote = models.db_manager.get_quote(quote_id)
    if not quote:
        flash("견적서를 찾을 수 없습니다.")
        return redirect(url_for("admin.quotes"))
    suppliers = models.db_manager.get_suppliers()
    supplier = None
    if quote.get("supplier_id"):
        supplier = models.db_manager.get_supplier(quote["supplier_id"])
    return render_template("admin/quote_edit.html", quote=quote, suppliers=suppliers, supplier=supplier)


@admin_bp.route("/quotes/<int:quote_id>/edit", methods=["POST"])
@admin_required
def quote_edit_post(quote_id):
    if not models.db_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.quotes"))

    import json
    try:
        parsed_data = json.loads(request.form.get("parsed_data", "{}"))
    except Exception:
        parsed_data = {}
    try:
        items = json.loads(request.form.get("items", "[]"))
    except Exception:
        items = []

    supplier_id = request.form.get("supplier_id", "") or None
    if supplier_id:
        supplier_id = int(supplier_id)

    models.db_manager.update_quote(
        quote_id,
        parsed_data=parsed_data,
        items=items,
        supplier_id=supplier_id,
        recipient=request.form.get("recipient", "").strip(),
        notes=request.form.get("notes", "").strip(),
        memo=request.form.get("memo", ""),
    )
    flash("견적서 수정 완료")
    return redirect(url_for("admin.quote_edit", quote_id=quote_id))


@admin_bp.route("/quotes/<int:quote_id>/approve", methods=["POST"])
@admin_required
def quote_approve(quote_id):
    """견적서 승인 → 캠페인 자동 등록"""
    if not models.db_manager:
        return jsonify({"ok": False, "message": "DB 미설정"})

    import json
    import uuid
    import re as _re
    from modules.utils import today_str, safe_int

    quote = models.db_manager.get_quote(quote_id)
    if not quote:
        return jsonify({"ok": False, "message": "견적서 없음"})

    parsed = quote.get("parsed_data") or {}
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except Exception:
            parsed = {}

    campaign_id = str(uuid.uuid4())[:8]
    data = {"캠페인ID": campaign_id, "등록일": today_str(), "상태": "모집중", "완료수량": "0"}

    direct_fields = [
        "상품링크", "플랫폼", "업체명", "상품명", "캠페인유형",
        "총수량", "일수량", "진행일수", "상품금액", "리뷰비",
        "옵션", "유입방식", "키워드", "키워드위치",
        "당일발송", "발송마감", "택배사", "3PL사용",
        "주말작업", "리뷰제공", "중복허용", "구매가능시간", "메모",
    ]
    for f in direct_fields:
        val = parsed.get(f, "")
        if val:
            data[f] = str(val)

    from modules.utils import extract_product_codes
    product_link = data.get("상품링크", "")
    if product_link:
        codes = extract_product_codes(product_link)
        if codes:
            data["상품코드"] = codes

    total = safe_int(data.get("총수량", 0))
    daily_str = data.get("일수량", "").strip()
    days = safe_int(data.get("진행일수", 0))
    if total > 0 and days > 0 and daily_str:
        range_match = _re.match(r"(\d+)\s*[-~]\s*(\d+)", daily_str)
        if range_match:
            lo, hi = int(range_match.group(1)), int(range_match.group(2))
        else:
            lo = hi = safe_int(daily_str)
        if lo > 0 and hi >= lo:
            schedule = _generate_schedule(total, lo, hi, days)
            data["일정"] = schedule
            data["시작일"] = today_str()

    try:
        models.db_manager.create_campaign(data)
        models.db_manager.approve_quote(quote_id, campaign_id)
        display_name = data.get("캠페인명", "").strip() or data.get("상품명", "")
        return jsonify({"ok": True, "campaign_id": campaign_id, "message": f"캠페인 '{display_name}' 등록 완료"})
    except Exception as e:
        logger.error(f"견적서 승인/캠페인 등록 에러: {e}")
        return jsonify({"ok": False, "message": str(e)})


@admin_bp.route("/quotes/<int:quote_id>/reject", methods=["POST"])
@admin_required
def quote_reject(quote_id):
    if not models.db_manager:
        return jsonify({"ok": False, "message": "DB 미설정"})
    models.db_manager.update_quote(quote_id, status="거절")
    return jsonify({"ok": True})


@admin_bp.route("/quotes/<int:quote_id>", methods=["DELETE"])
@admin_required
def quote_delete(quote_id):
    if not models.db_manager:
        return jsonify({"ok": False, "message": "DB 미설정"})
    models.db_manager.delete_quote(quote_id)
    return jsonify({"ok": True})


@admin_bp.route("/api/quotes/parse", methods=["POST"])
@admin_required
def api_quote_parse():
    """AI 릴레이로 요청서 텍스트 파싱"""
    import json

    data = request.get_json(silent=True) or {}
    raw_text = data.get("raw_text", "").strip()
    if not raw_text:
        return jsonify({"ok": False, "message": "요청서 텍스트가 비어있습니다."})

    if not models.ai_handler:
        return jsonify({"ok": False, "message": "AI 릴레이가 설정되지 않았습니다."})

    prompt = QUOTE_PARSE_PROMPT.replace("{raw_text}", raw_text)

    try:
        headers = {}
        if models.ai_handler.api_key:
            headers["X-API-Key"] = models.ai_handler.api_key

        resp = _requests.post(
            f"{models.ai_handler.relay_url}/ai",
            json={"prompt": prompt},
            headers=headers,
            timeout=90,
        )
        resp.raise_for_status()
        ai_response = resp.json().get("response", "")

        cleaned = ai_response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

        parsed = json.loads(cleaned)
        return jsonify({"ok": True, "parsed": parsed})

    except json.JSONDecodeError:
        logger.error(f"AI 파싱 JSON 실패: {ai_response[:200] if 'ai_response' in locals() else 'N/A'}")
        return jsonify({"ok": False, "message": "AI 응답을 JSON으로 변환할 수 없습니다.", "raw": ai_response if 'ai_response' in locals() else ""})
    except _requests.Timeout:
        return jsonify({"ok": False, "message": "AI 릴레이 타임아웃 (90초)"})
    except Exception as e:
        logger.error(f"AI 파싱 에러: {e}")
        return jsonify({"ok": False, "message": str(e)})


@admin_bp.route("/quotes/<int:quote_id>/preview")
@admin_required
def quote_preview(quote_id):
    if not models.db_manager:
        flash("시스템 초기화 중입니다.")
        return redirect(url_for("admin.quotes"))
    quote = models.db_manager.get_quote(quote_id)
    if not quote:
        flash("견적서를 찾을 수 없습니다.")
        return redirect(url_for("admin.quotes"))
    supplier = None
    if quote.get("supplier_id"):
        supplier = models.db_manager.get_supplier(quote["supplier_id"])
    return render_template("admin/quote_preview.html", quote=quote, supplier=supplier)

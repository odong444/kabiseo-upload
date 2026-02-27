"""
reviewer.py - 리뷰어 웹 UI Blueprint

캠페인 목록, 신청, 가이드, 캡쳐 업로드 등 리뷰어 플로우 전체.
"""

import logging
from flask import Blueprint, render_template, request, jsonify

import models

logger = logging.getLogger(__name__)

reviewer_bp = Blueprint("reviewer", __name__)


# ════════════════════════════════════════
# 페이지 라우트
# ════════════════════════════════════════

@reviewer_bp.route("/campaigns")
def campaigns():
    """캠페인 목록"""
    return render_template("campaigns.html")


@reviewer_bp.route("/c/<campaign_id>")
def campaign_detail(campaign_id):
    """캠페인 상세 (= 홍보링크 랜딩)"""
    return render_template("campaign_detail.html", campaign_id=campaign_id)


@reviewer_bp.route("/apply/<campaign_id>")
def apply_page(campaign_id):
    """캠페인 신청"""
    return render_template("apply.html", campaign_id=campaign_id)


@reviewer_bp.route("/task/<int:progress_id>")
def task_page(progress_id):
    """가이드 + 구매캡쳐 업로드"""
    return render_template("task.html", progress_id=progress_id)


@reviewer_bp.route("/task/<int:progress_id>/review")
def task_review_page(progress_id):
    """리뷰캡쳐 제출"""
    return render_template("task_review.html", progress_id=progress_id)


@reviewer_bp.route("/my")
def my_page():
    """내 작업내역"""
    return render_template("my.html")


# ════════════════════════════════════════
# API 엔드포인트
# ════════════════════════════════════════

@reviewer_bp.route("/api/campaigns")
def api_campaigns():
    """캠페인 목록 JSON (campaign_id 포함)"""
    if not models.campaign_manager or not models.db_manager:
        return jsonify([])

    from modules.utils import safe_int, is_within_buy_time

    name = request.args.get("name", "").strip()
    phone = request.args.get("phone", "").strip()

    all_campaigns = models.campaign_manager.get_all_campaigns()
    if not all_campaigns:
        return jsonify([])

    actual_counts = {}
    today_counts = {}
    reviewer_items = []
    try:
        actual_counts = models.db_manager.count_all_campaigns()
    except Exception:
        pass
    try:
        today_counts = models.db_manager.count_today_all_campaigns()
    except Exception:
        pass
    if name and phone:
        try:
            reviewer_items = models.db_manager.search_by_name_phone(name, phone)
        except Exception:
            pass

    from datetime import date as _date, datetime as _datetime

    cards = []
    for c in all_campaigns:
        campaign_id = c.get("캠페인ID", "")
        status = c.get("상태", "")
        if c.get("공개여부", "").strip().upper() in ("N",):
            continue
        if status not in ("모집중", "진행중", ""):
            continue
        # 시작일이 미래면 목록에서 제외
        start_str = (c.get("시작일") or "").strip()
        if start_str:
            try:
                start_date = _datetime.strptime(start_str, "%Y-%m-%d").date()
                if start_date > _date.today():
                    continue
            except ValueError:
                pass

        total = safe_int(c.get("총수량", 0))
        done = actual_counts.get(campaign_id, 0) or safe_int(c.get("완료수량", 0))
        total_remaining = total - done

        is_closed = total_remaining <= 0
        closed_reason = "마감" if is_closed else ""

        daily_target = models.campaign_manager._get_today_target(c)
        today_done = today_counts.get(campaign_id, 0)
        daily_full = daily_target > 0 and today_done >= daily_target
        if not is_closed and daily_full:
            is_closed = True
            closed_reason = "금일마감"

        if daily_target > 0:
            remaining = min(total_remaining, daily_target - today_done)
        else:
            remaining = total_remaining

        buy_time_str = c.get("구매가능시간", "").strip()
        buy_time_active = is_within_buy_time(buy_time_str)

        card = {
            "campaign_id": campaign_id,
            "name": c.get("캠페인명", "") or c.get("상품명", ""),
            "store": c.get("업체명", ""),
            "total": total,
            "remaining": max(remaining, 0),
            "urgent": not is_closed and 0 < remaining <= 5,
            "buy_time": buy_time_str,
            "buy_time_closed": not buy_time_active,
            "product_price": str(c.get("상품금액", "") or c.get("결제금액", "")),
            "review_fee": str(c.get("리뷰비", "") or ""),
            "platform": str(c.get("플랫폼", "") or c.get("캠페인유형", "") or ""),
            "closed": is_closed,
            "closed_reason": closed_reason,
            "max_per_person_daily": safe_int(c.get("1인일일제한", 0)),
        }

        # 내 진행 이력
        if campaign_id and reviewer_items:
            my_history = []
            for item in reviewer_items:
                if item.get("캠페인ID") == campaign_id:
                    sid = item.get("아이디", "").strip()
                    st = item.get("상태", "")
                    if sid and st not in ("타임아웃취소", "취소"):
                        my_history.append({"id": sid, "status": st, "progress_id": item.get("id")})
            if my_history:
                card["my_history"] = my_history

        cards.append(card)

    # 활성 먼저, 마감 뒤로
    cards.sort(key=lambda x: (x["closed"], x["name"]))
    return jsonify(cards)


@reviewer_bp.route("/api/campaign/<campaign_id>")
def api_campaign_detail(campaign_id):
    """캠페인 상세 JSON"""
    if not models.campaign_manager:
        return jsonify({"error": "not_ready"}), 503

    campaign = models.campaign_manager.get_campaign_by_id(campaign_id)
    if not campaign:
        return jsonify({"error": "not_found"}), 404

    # 잔여 수량 계산
    from modules.utils import safe_int, is_within_buy_time
    total = safe_int(campaign.get("총수량", 0))
    try:
        counts = models.db_manager.count_all_campaigns()
        done = counts.get(campaign_id, 0)
    except Exception:
        done = safe_int(campaign.get("완료수량", 0))
    remaining = total - done

    # 일일 잔여
    daily_remaining = -1
    if models.campaign_manager:
        daily_remaining = models.campaign_manager.check_daily_remaining(campaign_id)

    result = {
        "campaign_id": campaign_id,
        "name": campaign.get("캠페인명", "") or campaign.get("상품명", ""),
        "store": campaign.get("업체명", ""),
        "product_name": campaign.get("상품명", ""),
        "product_link": campaign.get("상품링크", ""),
        "product_image": campaign.get("상품이미지", ""),
        "product_price": campaign.get("상품금액", "") or campaign.get("결제금액", ""),
        "review_fee": campaign.get("리뷰비", ""),
        "platform": campaign.get("플랫폼", "") or campaign.get("캠페인유형", ""),
        "total": total,
        "remaining": max(remaining, 0),
        "daily_remaining": daily_remaining,
        "keyword": campaign.get("키워드", ""),
        "options": campaign.get("옵션", ""),
        "option_list": campaign.get("옵션목록", "[]"),
        "buy_time": campaign.get("구매가능시간", ""),
        "buy_time_active": is_within_buy_time(campaign.get("구매가능시간", "")),
        "status": campaign.get("상태", ""),
        "campaign_guide": campaign.get("캠페인가이드", ""),
        "review_guide": campaign.get("리뷰가이드내용", ""),
        "extra_info": campaign.get("추가안내사항", ""),
        "campaign_type": campaign.get("캠페인유형", ""),
        "payment_amount": campaign.get("결제금액", ""),
        "dwell_time": campaign.get("체류시간", ""),
        "bookmark_required": campaign.get("상품찜필수", ""),
        "alert_required": campaign.get("알림받기필수", ""),
        "entry_method": campaign.get("유입방식", ""),
        "ship_memo_required": campaign.get("배송메모필수", ""),
        "ship_memo_content": campaign.get("배송메모내용", ""),
        "ship_memo_link": campaign.get("배송메모안내링크", ""),
        "max_per_person_daily": safe_int(campaign.get("1인일일제한", 0)),
    }

    # 리뷰어 이력 추가 (로그인한 경우)
    name = request.args.get("name", "").strip()
    phone = request.args.get("phone", "").strip()
    if name and phone:
        try:
            my_ids = models.db_manager.get_user_campaign_ids(name, phone, campaign_id)
            result["my_ids"] = my_ids
        except Exception:
            result["my_ids"] = []

    return jsonify(result)


@reviewer_bp.route("/api/apply", methods=["POST"])
def api_apply():
    """캠페인 신청 API"""
    if not models.reviewer_manager or not models.campaign_manager:
        return jsonify({"ok": False, "error": "시스템 초기화 중"}), 503

    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    phone = data.get("phone", "").strip()
    campaign_id = data.get("campaign_id", "").strip()
    store_ids = data.get("store_ids", [])

    if not name or not phone or not campaign_id or not store_ids:
        return jsonify({"ok": False, "error": "필수 항목이 누락되었습니다"}), 400

    campaign = models.campaign_manager.get_campaign_by_id(campaign_id)
    if not campaign:
        return jsonify({"ok": False, "error": "캠페인을 찾을 수 없습니다"}), 404

    # 정원 확인
    capacity = models.campaign_manager.check_capacity(campaign_id)
    if capacity < len(store_ids):
        return jsonify({"ok": False, "error": f"잔여 {capacity}자리입니다. 요청 수를 줄여주세요."}), 400

    # 일일 잔여 확인
    daily_remaining = models.campaign_manager.check_daily_remaining(campaign_id)
    if daily_remaining >= 0 and daily_remaining < len(store_ids):
        return jsonify({"ok": False, "error": f"금일 잔여 {daily_remaining}자리입니다."}), 400

    # 1인 일일 제한 확인
    from modules.utils import safe_int
    max_pp = safe_int(campaign.get("1인일일제한", 0))
    if max_pp > 0:
        already = models.db_manager.count_today_user_campaign(name, phone, campaign_id)
        if already + len(store_ids) > max_pp:
            remain = max(0, max_pp - already)
            return jsonify({"ok": False, "error": f"1인 하루 최대 {max_pp}건입니다. (잔여 {remain}건)"}), 400

    # 중복 체크 및 등록
    results = []
    for sid in store_ids:
        sid = sid.strip()
        if not sid:
            continue
        if models.reviewer_manager.check_duplicate(campaign_id, sid):
            results.append({"store_id": sid, "ok": False, "error": "이미 등록된 아이디"})
            continue

        try:
            progress_id = models.reviewer_manager.register(name, phone, campaign, sid)
            # 아이디 목록 업데이트
            models.db_manager.update_reviewer_store_ids(name, phone, sid)
            results.append({"store_id": sid, "ok": True, "progress_id": progress_id})
        except Exception as e:
            logger.error("신청 에러: %s", e, exc_info=True)
            results.append({"store_id": sid, "ok": False, "error": str(e)})

    success = [r for r in results if r.get("ok")]
    return jsonify({
        "ok": len(success) > 0,
        "results": results,
        "message": f"{len(success)}건 신청 완료" if success else "신청 실패",
    })


@reviewer_bp.route("/api/my")
def api_my():
    """내 작업내역 JSON"""
    name = request.args.get("name", "").strip()
    phone = request.args.get("phone", "").strip()
    if not name or not phone or not models.db_manager:
        return jsonify({"items": []})

    all_items = models.db_manager.search_by_name_phone(name, phone)

    items = []
    for item in all_items:
        status = item.get("상태", "")
        if status in ("타임아웃취소", "취소"):
            continue
        items.append({
            "id": item.get("id"),
            "campaign_id": item.get("캠페인ID", ""),
            "product_name": item.get("제품명", ""),
            "store_name": item.get("업체명", ""),
            "store_id": item.get("아이디", ""),
            "status": status,
            "date": item.get("날짜", ""),
            "purchase_capture": item.get("구매캡쳐링크", ""),
            "review_capture": item.get("리뷰캡쳐링크", ""),
            "review_fee": item.get("리뷰비", ""),
            "remark": item.get("비고", ""),
        })

    return jsonify({"items": items})


@reviewer_bp.route("/api/task/<int:progress_id>")
def api_task(progress_id):
    """태스크 상세 JSON (가이드 + 폼 데이터)"""
    if not models.db_manager:
        return jsonify({"error": "not_ready"}), 503

    row = models.db_manager.get_row_dict(progress_id)
    if not row:
        return jsonify({"error": "not_found"}), 404

    campaign_id = row.get("캠페인ID", "")
    campaign = models.db_manager.get_campaign_by_id(campaign_id) if campaign_id else {}

    # 이전 정보 로드
    name = row.get("진행자이름", "")
    phone = row.get("진행자연락처", "")
    prev_info = models.db_manager.get_user_prev_info(name, phone) if name and phone else {}

    # 같은 캠페인의 형제 아이디들 로드
    siblings = []
    if campaign_id and name and phone:
        all_items = models.db_manager.search_by_name_phone(name, phone)
        for item in all_items:
            if item.get("캠페인ID") == campaign_id and item.get("상태") not in ("타임아웃취소", "취소"):
                siblings.append({
                    "id": item.get("id"),
                    "store_id": item.get("아이디", ""),
                    "status": item.get("상태", ""),
                    "order_number": item.get("주문번호", ""),
                    "recipient_name": item.get("수취인명", ""),
                    "payment_amount": item.get("결제금액", ""),
                    "address": item.get("주소", ""),
                    "bank": item.get("은행", ""),
                    "account": item.get("계좌", ""),
                    "depositor": item.get("예금주", ""),
                    "nickname": item.get("닉네임", ""),
                    "remark": item.get("비고", ""),
                })

    result = {
        "id": progress_id,
        "campaign_id": campaign_id,
        "product_name": row.get("제품명", ""),
        "store_name": row.get("업체명", ""),
        "store_id": row.get("아이디", ""),
        "status": row.get("상태", ""),
        "date": row.get("날짜", ""),
        "created_at": row.get("created_at_iso", ""),
        "timeout_seconds": 1800,
        "purchase_capture": row.get("구매캡쳐링크", ""),
        "review_capture": row.get("리뷰캡쳐링크", ""),
        "remark": row.get("비고", ""),
        # 폼 데이터
        "recipient_name": row.get("수취인명", ""),
        "phone": row.get("연락처", ""),
        "bank": row.get("은행", ""),
        "account": row.get("계좌", ""),
        "depositor": row.get("예금주", ""),
        "address": row.get("주소", ""),
        "order_number": row.get("주문번호", ""),
        "payment_amount": row.get("결제금액", ""),
        "nickname": row.get("닉네임", ""),
        # 이전 정보 (자동입력용)
        "prev_info": prev_info,
        # 계좌 프리셋 (과거 등록 계좌 목록)
        "bank_presets": models.db_manager.get_user_bank_presets(name, phone) if name and phone else [],
        # 캠페인 가이드
        "campaign": {
            "name": campaign.get("캠페인명", "") or campaign.get("상품명", ""),
            "product_link": campaign.get("상품링크", ""),
            "product_image": campaign.get("상품이미지", ""),
            "keyword": campaign.get("키워드", ""),
            "options": campaign.get("옵션", ""),
            "campaign_guide": campaign.get("캠페인가이드", ""),
            "review_guide": campaign.get("리뷰가이드내용", ""),
            "extra_info": campaign.get("추가안내사항", ""),
            "campaign_type": campaign.get("캠페인유형", ""),
            "payment_amount": campaign.get("결제금액", ""),
            "dwell_time": campaign.get("체류시간", ""),
            "bookmark_required": campaign.get("상품찜필수", ""),
            "alert_required": campaign.get("알림받기필수", ""),
            "entry_method": campaign.get("유입방식", ""),
            "ship_memo_required": campaign.get("배송메모필수", ""),
            "ship_memo_content": campaign.get("배송메모내용", ""),
            "ship_memo_link": campaign.get("배송메모안내링크", ""),
            "review_fee": campaign.get("리뷰비", ""),
        } if campaign else {},
        "siblings": siblings,
    }

    # 사진 세트
    photo_set_number = row.get("사진세트")
    if photo_set_number and campaign_id:
        try:
            all_sets = models.db_manager.get_campaign_photo_sets(campaign_id)
            photos = all_sets.get(photo_set_number, [])
            result["photo_set"] = [p["url"] for p in photos]
        except Exception:
            result["photo_set"] = []
    else:
        result["photo_set"] = []

    return jsonify(result)


@reviewer_bp.route("/api/verify-capture", methods=["POST"])
def api_verify_capture():
    """AI 캡쳐 검수 (Drive 업로드 전, bytes만)"""
    file = request.files.get("capture")
    capture_type = request.form.get("capture_type", "purchase")
    campaign_id = request.form.get("campaign_id", "")

    if not file or file.filename == "":
        return jsonify({"ok": False, "error": "파일을 선택해주세요"}), 400

    try:
        image_bytes = file.read()
        mime_type = file.content_type or "image/jpeg"

        # AI 지침 + 캠페인 기준정보 조회
        ai_instructions = ""
        campaign_info = None
        if models.db_manager:
            parts = []
            global_key = "ai_global_purchase" if capture_type == "purchase" else "ai_global_review"
            global_instr = models.db_manager.get_setting(global_key, "")
            if global_instr:
                parts.append(global_instr)
            if campaign_id:
                campaign = models.db_manager.get_campaign_by_id(campaign_id)
                if campaign:
                    field = "AI구매검수지침" if capture_type == "purchase" else "AI리뷰검수지침"
                    camp_instr = campaign.get(field, "") or campaign.get("AI검수지침", "")
                    if camp_instr:
                        parts.append(camp_instr)
                    campaign_info = {
                        "상품명": campaign.get("상품명", ""),
                        "업체명": campaign.get("업체명", ""),
                        "플랫폼": campaign.get("플랫폼", ""),
                        "상품금액": campaign.get("상품금액", ""),
                        "결제금액": campaign.get("결제금액", ""),
                        "옵션": campaign.get("옵션", ""),
                        "캠페인유형": campaign.get("캠페인유형", ""),
                    }
            ai_instructions = "\n".join(parts)

        # 리뷰 검수 시 사진 세트 할당 여부 확인 → 사진 필수 조건 추가
        if capture_type == "review" and models.db_manager:
            pid = request.form.get("progress_id", "")
            if pid:
                try:
                    row = models.db_manager._fetchone(
                        "SELECT photo_set_number FROM progress WHERE id = %s", (int(pid),)
                    )
                    if row and row.get("photo_set_number"):
                        ai_instructions += (
                            "\n\n[사진 첨부 필수 조건]\n"
                            "이 리뷰어는 리뷰용 참고 사진을 제공받았습니다.\n"
                            "리뷰에 사진이 반드시 포함되어야 합니다.\n"
                            "리뷰 캡쳐에 사진이 보이지 않으면 '사진_미첨부'를 문제점에 추가하세요."
                        )
                except Exception:
                    pass

        from modules.capture_verifier import verify_capture_from_bytes
        result = verify_capture_from_bytes(image_bytes, mime_type, capture_type, ai_instructions, campaign_info)

        return jsonify({
            "ok": True,
            "result": result["result"],
            "reason": result["reason"],
            "parsed": result.get("parsed", {}),
        })
    except Exception as e:
        logger.error("AI 검수 에러: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": f"AI 검수 실패: {e}"}), 500


@reviewer_bp.route("/api/task/<int:progress_id>/extend-time", methods=["POST"])
def api_extend_time(progress_id):
    """양식 제출 시간 연장 (타임아웃 리셋)"""
    if not models.db_manager:
        return jsonify({"ok": False, "error": "시스템 초기화 중"}), 503
    try:
        from app import _touch_reviewer_by_row
        _touch_reviewer_by_row(progress_id)
        from modules.utils import now_kst
        new_deadline = now_kst() + __import__('datetime').timedelta(seconds=1800)
        logger.info("시간 연장: progress=%s", progress_id)
        return jsonify({"ok": True, "new_deadline": new_deadline.isoformat()})
    except Exception as e:
        logger.error("시간 연장 에러: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@reviewer_bp.route("/api/chat/unread")
def api_chat_unread():
    """읽지 않은 채팅 메시지 수 (bot 메시지만)"""
    name = request.args.get("name", "").strip()
    phone = request.args.get("phone", "").strip()
    last_read = request.args.get("last_read", "0")
    if not name or not phone:
        return jsonify({"count": 0})
    try:
        ts = float(last_read)
    except (ValueError, TypeError):
        ts = 0
    reviewer_id = models.state_store.make_id(name, phone)
    count = 0
    if models.db_manager:
        try:
            count = models.db_manager.count_chat_unread(reviewer_id, ts)
        except Exception:
            pass
    return jsonify({"count": count})


@reviewer_bp.route("/api/task/<int:progress_id>/purchase", methods=["POST"])
def api_task_purchase(progress_id):
    """구매완료 제출 (캡쳐 + 폼데이터)"""
    if not models.db_manager or not models.drive_uploader:
        return jsonify({"ok": False, "error": "시스템 초기화 중"}), 503

    file = request.files.get("capture")
    if not file or file.filename == "":
        return jsonify({"ok": False, "error": "캡쳐 파일이 필요합니다"}), 400

    row = models.db_manager.get_row_dict(progress_id)
    if not row:
        return jsonify({"ok": False, "error": "건을 찾을 수 없습니다"}), 404

    try:
        # 1. Drive 업로드
        from app import _make_upload_filename
        filename = _make_upload_filename("purchase", progress_id, file.filename)
        file_bytes = file.read()
        drive_link = models.drive_uploader.upload(
            file_bytes, filename, file.content_type or "image/jpeg", "purchase",
            f"purchase_progress{progress_id}"
        )

        # 2. 폼 데이터 업데이트
        form_fields = {}
        for key in ("recipient_name", "phone", "bank", "account", "depositor",
                     "address", "order_number", "payment_amount", "nickname"):
            val = request.form.get(key, "").strip()
            if val:
                form_fields[key] = val

        # DB 컬럼명 매핑
        field_map = {
            "recipient_name": "수취인명", "phone": "연락처",
            "bank": "은행", "account": "계좌", "depositor": "예금주",
            "address": "주소", "order_number": "주문번호",
            "payment_amount": "결제금액", "nickname": "닉네임",
        }
        for api_key, sheet_key in field_map.items():
            val = form_fields.get(api_key, "")
            if val:
                models.db_manager.update_progress_field(progress_id, sheet_key, val)

        # 3. 캡쳐 URL + 상태 업데이트
        models.db_manager.update_after_upload("purchase", progress_id, drive_link)

        # 4. AI 검수 트리거 (백그라운드)
        from app import _trigger_ai_verify
        _trigger_ai_verify("purchase", progress_id, drive_link)

        return jsonify({"ok": True, "message": "구매 캡쳐 제출 완료!"})
    except Exception as e:
        logger.error("구매 제출 에러: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": f"제출 실패: {e}"}), 500


@reviewer_bp.route("/api/task/<int:progress_id>/review", methods=["POST"])
def api_task_review(progress_id):
    """리뷰 캡쳐 제출"""
    if not models.db_manager or not models.drive_uploader:
        return jsonify({"ok": False, "error": "시스템 초기화 중"}), 503

    file = request.files.get("capture")
    if not file or file.filename == "":
        return jsonify({"ok": False, "error": "캡쳐 파일이 필요합니다"}), 400

    row = models.db_manager.get_row_dict(progress_id)
    if not row:
        return jsonify({"ok": False, "error": "건을 찾을 수 없습니다"}), 404

    try:
        from app import _make_upload_filename
        filename = _make_upload_filename("review", progress_id, file.filename)
        file_bytes = file.read()
        drive_link = models.drive_uploader.upload(
            file_bytes, filename, file.content_type or "image/jpeg", "review",
            f"review_progress{progress_id}"
        )

        models.db_manager.update_after_upload("review", progress_id, drive_link)

        from app import _trigger_ai_verify
        _trigger_ai_verify("review", progress_id, drive_link)

        return jsonify({"ok": True, "message": "리뷰 캡쳐 제출 완료!"})
    except Exception as e:
        logger.error("리뷰 제출 에러: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": f"제출 실패: {e}"}), 500


@reviewer_bp.route("/api/user/store-ids")
def api_user_store_ids():
    """리뷰어가 사용한 모든 아이디 목록"""
    name = request.args.get("name", "").strip()
    phone = request.args.get("phone", "").strip()
    if not name or not phone or not models.db_manager:
        return jsonify({"ids": []})

    ids = list(models.db_manager.get_used_store_ids(name, phone))
    return jsonify({"ids": sorted(ids)})

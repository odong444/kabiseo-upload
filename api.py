"""
api.py - REST API (서버PC 카카오톡 연동용)

API_KEY 인증 기반.
"""

import os
import logging
from functools import wraps
from flask import Blueprint, request, jsonify

import models

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__, url_prefix="/api")

API_KEY = os.environ.get("API_KEY", "")
WEB_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")


def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-API-Key", "") or request.args.get("api_key", "")
        if not API_KEY:
            return f(*args, **kwargs)  # API_KEY 미설정 시 인증 skip
        if key != API_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


# ──────── 캠페인 API ────────

@api_bp.route("/campaigns/need-recruit", methods=["GET"])
@require_api_key
def campaigns_need_recruit():
    """홍보 필요한 캠페인 + 모집글"""
    if not models.campaign_manager:
        return jsonify({"error": "시스템 초기화 중"}), 503

    web_url = f"https://{WEB_URL}" if WEB_URL else request.host_url.rstrip("/")
    data = models.campaign_manager.get_needs_recruit(web_url)
    return jsonify(data)


@api_bp.route("/campaigns/<campaign_id>/recruited", methods=["POST"])
@require_api_key
def campaign_recruited(campaign_id):
    """홍보 완료 보고"""
    if not models.campaign_manager:
        return jsonify({"error": "시스템 초기화 중"}), 503

    data = request.get_json(silent=True) or {}
    logger.info(f"홍보 완료 보고: {campaign_id} - {data}")
    return jsonify({"status": "ok", "campaign_id": campaign_id})


@api_bp.route("/campaigns/<campaign_id>/status", methods=["GET"])
@require_api_key
def campaign_status(campaign_id):
    """캠페인 달성률"""
    if not models.campaign_manager:
        return jsonify({"error": "시스템 초기화 중"}), 503

    stats = models.campaign_manager.get_campaign_stats(campaign_id)
    if not stats:
        return jsonify({"error": "캠페인을 찾을 수 없습니다"}), 404
    return jsonify(stats)


@api_bp.route("/campaigns/<campaign_id>/recruit-targets", methods=["GET"])
@require_api_key
def campaign_recruit_targets(campaign_id):
    """기존리뷰어 모집 대상 목록 (카카오 친구 + 미발송 + 미참여)"""
    if not models.db_manager:
        return jsonify({"error": "시스템 초기화 중"}), 503

    limit = request.args.get("limit", 50, type=int)
    targets = models.db_manager.get_recruit_targets(campaign_id, limit=limit)
    return jsonify({"ok": True, "targets": targets, "count": len(targets)})


@api_bp.route("/campaigns/<campaign_id>/recruit-sent", methods=["POST"])
@require_api_key
def campaign_recruit_sent(campaign_id):
    """기존리뷰어 모집 발송 기록"""
    if not models.db_manager:
        return jsonify({"error": "시스템 초기화 중"}), 503

    data = request.get_json(silent=True) or {}
    name = data.get("name", "")
    phone = data.get("phone", "")
    if not name or not phone:
        return jsonify({"ok": False, "error": "name, phone 필수"}), 400

    models.db_manager.log_recruit_send(campaign_id, name, phone)
    return jsonify({"ok": True})


@api_bp.route("/campaigns/all", methods=["GET"])
@require_api_key
def campaigns_all():
    """전체 캠페인 목록"""
    if not models.campaign_manager:
        return jsonify({"error": "시스템 초기화 중"}), 503

    campaigns = models.campaign_manager.get_all_campaigns()
    return jsonify({"campaigns": campaigns})


@api_bp.route("/campaigns", methods=["POST"])
@require_api_key
def campaign_create():
    """캠페인 등록"""
    if not models.db_manager:
        return jsonify({"error": "시스템 초기화 중"}), 503

    data = request.get_json(silent=True) or {}
    logger.info(f"캠페인 등록 요청: {data}")
    # TODO: 캠페인 시트에 행 추가
    return jsonify({"status": "ok", "message": "캠페인 등록됨"})


@api_bp.route("/campaigns/<campaign_id>", methods=["PUT"])
@require_api_key
def campaign_update(campaign_id):
    """캠페인 수정"""
    data = request.get_json(silent=True) or {}
    logger.info(f"캠페인 수정: {campaign_id} - {data}")
    return jsonify({"status": "ok", "campaign_id": campaign_id})


@api_bp.route("/campaigns/<campaign_id>/close", methods=["POST"])
@require_api_key
def campaign_close(campaign_id):
    """캠페인 마감"""
    logger.info(f"캠페인 마감: {campaign_id}")
    return jsonify({"status": "ok", "campaign_id": campaign_id, "message": "마감 처리됨"})

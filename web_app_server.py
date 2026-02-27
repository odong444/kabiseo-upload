"""
카카오톡 홍보 + 알림 발신 전용 웹 대시보드

Flask 서버 + 홍보 사이클 + 태스크 큐(친구추가/독촉/알림).
채팅방 감지/자동 응답 기능은 제거됨.
python web_app.py 로 실행합니다.
"""

import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

import json
import logging
import threading
import time
from collections import deque
from datetime import datetime
from modules.utils import now_kst
from pathlib import Path

from functools import wraps
from flask import Flask, jsonify, render_template, request

# 프로젝트 루트
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.utils import setup_logger, load_config
from modules.stats_tracker import StatsTracker
from modules.task_queue import TaskQueue

app = Flask(__name__)
logger = setup_logger("web_app")

# ---------------------------------------------------------------------------
# 글로벌 상태
# ---------------------------------------------------------------------------
stats_tracker = StatsTracker()
task_queue = TaskQueue()
controller = None  # KakaoController 인스턴스
promoter = None    # OpenChatPromoter 인스턴스
monitoring_active = False
start_time: datetime = None

# 홍보 전용 스레드
promotion_active = False
_promotion_thread: threading.Thread = None
_promotion_stop_event = threading.Event()

# 최근 로그를 메모리에 보관 (대시보드 표시용)
log_buffer: deque = deque(maxlen=200)


class DashboardLogHandler(logging.Handler):
    """로그 메시지를 메모리 버퍼에 저장하는 핸들러."""

    def emit(self, record):
        try:
            log_buffer.append({
                "timestamp": self.format(record).split("]")[0].strip("["),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            })
        except Exception:
            pass


# 대시보드 로그 핸들러를 루트 로거에 등록
_dash_handler = DashboardLogHandler()
_dash_handler.setLevel(logging.INFO)
_dash_handler.setFormatter(logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
logging.getLogger().setLevel(logging.INFO)
logging.getLogger().addHandler(_dash_handler)


# ---------------------------------------------------------------------------
# 헬퍼 — 설정
# ---------------------------------------------------------------------------
def _config_path() -> str:
    return str(PROJECT_ROOT / "config.json")


def _load_config() -> dict:
    return load_config(_config_path())


def _save_config(cfg: dict):
    with open(_config_path(), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _update_config_promotion(enabled: bool):
    """config.json의 promotion.enabled를 업데이트."""
    cfg = _load_config()
    if "promotion" not in cfg:
        cfg["promotion"] = {}
    cfg["promotion"]["enabled"] = enabled
    _save_config(cfg)


def _get_api_key() -> str:
    """config.json에서 API 키 로드."""
    return _load_config().get("upload_server", {}).get("api_key", "")


def require_api_key(f):
    """X-API-Key 헤더 검증 데코레이터."""
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-API-Key", "")
        expected = _get_api_key()
        if not expected or key != expected:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# 헬퍼 — 오픈채팅방 관리
# ---------------------------------------------------------------------------
CHATROOMS_PATH = PROJECT_ROOT / "data" / "open_chatrooms.json"


def _load_chatrooms() -> dict:
    try:
        if CHATROOMS_PATH.exists():
            return json.loads(CHATROOMS_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"categories": [], "rooms": []}


def _save_chatrooms(data: dict):
    CHATROOMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHATROOMS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 홍보 전용 백그라운드 루프
# ---------------------------------------------------------------------------
def _promotion_loop():
    """홍보 전용 백그라운드 스레드 루프.

    60초마다 깨어나서 캠페인 확인 → 태스크 큐에 promotion 태스크 등록.
    직접 카카오톡을 조작하지 않고, 태스크 큐 워커가 순차 처리.
    캠페인별 카테고리로 채팅방 매칭.
    """
    global promoter

    logger.info("홍보 전용 루프 시작")
    last_promotion_time = 0

    cfg = _load_config()
    promo_config = cfg.get("promotion", {})
    promotion_interval = promo_config.get("check_interval_minutes", 10) * 60

    while not _promotion_stop_event.is_set():
        try:
            if promoter and promotion_active:
                now = time.time()
                if now - last_promotion_time > promotion_interval:
                    submitted = _submit_promotion_tasks()
                    if submitted > 0:
                        logger.info("=== 홍보 태스크 %d건 등록 ===", submitted)
                    last_promotion_time = now
        except Exception as e:
            import traceback
            logger.error("홍보 루프 오류: %s", e)
            logger.error("트레이스백:\n%s", traceback.format_exc())

        # 60초 대기 (stop_event로 즉시 종료 가능)
        _promotion_stop_event.wait(60)

    logger.info("홍보 전용 루프 종료")


def _submit_promotion_tasks() -> int:
    """캠페인별 카테고리 매칭으로 홍보 태스크를 태스크 큐에 등록.

    Returns:
        등록된 태스크 수
    """
    if not promoter:
        return 0

    # 활성 시간 체크
    if not promoter.should_run_now():
        return 0

    # Railway API에서 홍보 필요 캠페인 가져오기
    campaigns = promoter.fetch_campaigns()
    if not campaigns:
        logger.info("홍보할 캠페인 없음")
        return 0

    # 오픈채팅방 목록 로드
    chatroom_data = _load_chatrooms()
    rooms = chatroom_data.get("rooms", [])
    enabled_rooms = [r for r in rooms if r.get("enabled")]
    if not enabled_rooms:
        logger.info("활성화된 채팅방 없음")
        return 0

    submitted = 0
    for campaign in campaigns:
        campaign_id = campaign.get("캠페인ID", "")
        product_name = campaign.get("상품명", "")
        recruit_msg = campaign.get("모집글", "")
        if not recruit_msg:
            continue

        # 캠페인별 홍보 카테고리 (쉼표 구분)
        campaign_categories = set()
        cat_str = campaign.get("_promo_categories", "")
        if cat_str:
            campaign_categories = {c.strip() for c in cat_str.split(",") if c.strip()}

        # 캠페인별 쿨다운 (분 단위, 기본 60분 → 시간으로 환산)
        campaign_cooldown_min = campaign.get("_promo_cooldown", 60) or 60

        for room in enabled_rooms:
            room_name = room.get("name", "")
            room_categories = set(room.get("categories", []))

            # 카테고리 매칭: 캠페인 카테고리가 있으면 교집합 확인
            if campaign_categories and not campaign_categories.intersection(room_categories):
                continue

            # 쿨다운 체크 (캠페인별 쿨다운 사용)
            cooldown_hours = campaign_cooldown_min / 60.0
            old_cooldown = promoter.cooldown_hours
            promoter.cooldown_hours = cooldown_hours
            should = promoter._should_promote(campaign_id, room_name)
            promoter.cooldown_hours = old_cooldown
            if not should:
                continue

            # 태스크 큐에 등록
            tid = task_queue.submit("promotion", {
                "room_name": room_name,
                "room_type": "open",
                "message": recruit_msg,
                "campaign_id": campaign_id,
            }, priority=TaskQueue.PRIORITY_LOW)

            if tid:
                # 홍보 이력 즉시 기록 (중복 방지)
                promoter._record_promotion(campaign_id, room_name)
                submitted += 1
                logger.info("홍보 태스크 등록: [%s] → [%s] (tid=%s)",
                            product_name, room_name, tid)

    return submitted


# ---------------------------------------------------------------------------
# 라우트 — 페이지
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    """대시보드 HTML."""
    return render_template("campaign_dashboard.html")


# ---------------------------------------------------------------------------
# 라우트 — 모니터링 API (홍보 + 발신 전용)
# ---------------------------------------------------------------------------

@app.route("/api/status")
def api_status():
    """모니터링 상태 + 통계 요약."""
    global monitoring_active, start_time

    uptime_seconds = 0
    if monitoring_active and start_time:
        uptime_seconds = int((now_kst() - start_time).total_seconds())

    today = stats_tracker.get_today_summary()
    chatrooms = stats_tracker.get_chatroom_stats(hours=24)

    return jsonify({
        "monitoring": monitoring_active,
        "uptime_seconds": uptime_seconds,
        "today": today,
        "chatrooms": chatrooms,
        "server_time": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "sheets_connected": False,
        "promotion_active": promotion_active,
    })


@app.route("/api/start", methods=["POST"])
def api_start():
    """홍보 + 발신 시스템 시작 (채팅방 감지/자동 응답 없음)."""
    global controller, promoter, monitoring_active, start_time
    global promotion_active, _promotion_thread, _promotion_stop_event

    if monitoring_active:
        return jsonify({"ok": False, "error": "이미 실행 중입니다."}), 400

    try:
        from modules.kakao_controller import KakaoController

        controller = KakaoController()
        if not controller.find_kakao_window():
            return jsonify({
                "ok": False,
                "error": "카카오톡 윈도우를 찾을 수 없습니다.",
            }), 500

        cfg = _load_config()

        # OpenChatPromoter 초기화
        promo_config = cfg.get("promotion", {})
        if promo_config.get("enabled", False):
            try:
                from modules.open_chat_promoter import OpenChatPromoter
                promoter = OpenChatPromoter(controller, cfg)
                promotion_active = True
                logger.info("OpenChatPromoter 초기화 완료")
            except Exception as pe:
                logger.warning("OpenChatPromoter 초기화 실패: %s", pe)
                promoter = None
        else:
            logger.info("홍보 비활성 상태 (config.promotion.enabled=false)")

        # 홍보 전용 백그라운드 스레드 시작
        _promotion_stop_event.clear()
        _promotion_thread = threading.Thread(
            target=_promotion_loop,
            daemon=True,
            name="PromotionLoop",
        )
        _promotion_thread.start()

        monitoring_active = True
        start_time = now_kst()
        logger.info("홍보+발신 시스템 시작 (채팅방 감지 없음)")
        return jsonify({"ok": True})

    except Exception as e:
        logger.error("시스템 시작 실패: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """홍보 + 발신 시스템 중지."""
    global controller, promoter, monitoring_active, start_time
    global promotion_active, _promotion_thread, _promotion_stop_event

    if not monitoring_active:
        return jsonify({"ok": False, "error": "실행 중이 아닙니다."}), 400

    try:
        # 홍보 스레드 중지
        promotion_active = False
        _promotion_stop_event.set()
        if _promotion_thread is not None:
            _promotion_thread.join(timeout=10)
            _promotion_thread = None

        promoter = None
        controller = None
        monitoring_active = False
        start_time = None
        logger.info("홍보+발신 시스템 중지")
        return jsonify({"ok": True})

    except Exception as e:
        logger.error("시스템 중지 실패: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    """설정 읽기/저장."""
    if request.method == "GET":
        cfg = _load_config()
        # oauth_token은 마스킹
        ai = cfg.get("ai", {})
        token = ai.get("oauth_token", "")
        if token:
            ai["oauth_token"] = token[:12] + "..." + token[-4:] if len(token) > 16 else "***"
        return jsonify(cfg)

    # POST: 설정 업데이트
    data = request.get_json(silent=True) or {}
    cfg = _load_config()

    # system_prompt 업데이트
    if "system_prompt" in data:
        cfg.setdefault("ai", {})["system_prompt"] = data["system_prompt"]

    # web 섹션 업데이트
    if "web" in data:
        cfg["web"] = {**cfg.get("web", {}), **data["web"]}

    # monitor 섹션 업데이트
    if "monitor" in data:
        cfg["monitor"] = {**cfg.get("monitor", {}), **data["monitor"]}

    # ai 섹션 (system_prompt 외)
    if "ai" in data:
        for key in ("cli_timeout", "max_history", "my_name"):
            if key in data["ai"]:
                cfg.setdefault("ai", {})[key] = data["ai"][key]

    _save_config(cfg)
    logger.info("설정 저장됨")
    return jsonify({"ok": True})


@app.route("/api/logs")
def api_logs():
    """최근 로그."""
    limit = request.args.get("limit", 100, type=int)
    logs = list(log_buffer)[-limit:]
    logs.reverse()  # 최신순
    return jsonify({"logs": logs})


@app.route("/api/events")
def api_events():
    """최근 이벤트 (DB)."""
    limit = request.args.get("limit", 50, type=int)
    events = stats_tracker.get_recent_events(limit=limit)
    return jsonify({"events": events})


# ---------------------------------------------------------------------------
# 라우트 — 채팅방 관리 API
# ---------------------------------------------------------------------------

@app.route("/api/chatrooms")
def api_chatrooms():
    """채팅방 목록."""
    data = _load_chatrooms()
    return jsonify({"rooms": data.get("rooms", [])})


@app.route("/api/chatrooms", methods=["POST"])
def api_chatrooms_add():
    """채팅방 추가."""
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "채팅방명 필수"}), 400

    data = _load_chatrooms()
    # 중복 체크
    for r in data["rooms"]:
        if r["name"] == name:
            return jsonify({"ok": False, "error": "이미 등록된 채팅방"}), 400

    room = {
        "name": name,
        "type": body.get("type", "open"),
        "categories": body.get("categories", []),
        "enabled": body.get("enabled", True),
        "active_hours": body.get("active_hours", ""),
        "cooldown_minutes": body.get("cooldown_minutes", 60),
        "last_sent": None,
    }
    data["rooms"].append(room)
    _save_chatrooms(data)
    return jsonify({"ok": True})


@app.route("/api/chatrooms/<path:name>", methods=["PUT"])
def api_chatrooms_update(name):
    """채팅방 수정."""
    body = request.get_json(silent=True) or {}
    data = _load_chatrooms()

    for r in data["rooms"]:
        if r["name"] == name:
            if "type" in body:
                r["type"] = body["type"]
            if "categories" in body:
                r["categories"] = body["categories"]
            if "enabled" in body:
                r["enabled"] = body["enabled"]
            if "active_hours" in body:
                r["active_hours"] = body["active_hours"]
            if "cooldown_minutes" in body:
                r["cooldown_minutes"] = body["cooldown_minutes"]
            _save_chatrooms(data)
            return jsonify({"ok": True})

    return jsonify({"ok": False, "error": "채팅방 없음"}), 404


@app.route("/api/chatrooms/delete", methods=["POST"])
def api_chatrooms_delete():
    """채팅방 삭제."""
    body = request.get_json(silent=True) or {}
    name = body.get("name", "")
    if not name:
        return jsonify({"ok": False, "error": "name 필수"}), 400
    import unicodedata
    data = _load_chatrooms()
    before = len(data["rooms"])
    # 유니코드 정규화 (NFC) 적용하여 비교
    norm_name = unicodedata.normalize("NFC", name.strip())
    data["rooms"] = [r for r in data["rooms"]
                     if unicodedata.normalize("NFC", r["name"].strip()) != norm_name]
    if len(data["rooms"]) == before:
        # 부분 매칭 시도 (앞뒤 공백, 보이지 않는 문자 등)
        data["rooms"] = [r for r in _load_chatrooms()["rooms"]
                         if r["name"].strip() != name.strip()]
    if len(data["rooms"]) == before:
        return jsonify({"ok": False, "error": "채팅방 없음"}), 404
    _save_chatrooms(data)
    return jsonify({"ok": True})


@app.route("/api/chatrooms/<path:name>/test", methods=["POST"])
def api_chatrooms_test(name):
    """테스트 발송 — 태스크 큐에 제출하여 순차 실행."""
    if not promotion_active:
        return jsonify({"ok": False, "error": "홍보가 중지 상태입니다. 먼저 홍보를 시작하세요."}), 403

    data = _load_chatrooms()
    room = None
    for r in data["rooms"]:
        if r["name"] == name:
            room = r
            break
    if not room:
        return jsonify({"ok": False, "error": "채팅방 없음"}), 404

    task_id = task_queue.submit("test_send", {
        "room_name": name,
        "room_type": room.get("type", "open"),
        "message": ",",
    }, priority=1)  # 긴급 우선순위로 테스트

    return jsonify({"ok": True, "task_id": task_id, "message": "태스크 큐에 등록됨"})


@app.route("/api/chatrooms/scan", methods=["POST"])
def api_chatrooms_scan():
    """열려있는 채팅창 스캔. mode=scan(전체교체) / mode=add(추가만)."""
    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "scan")
    task_id = task_queue.submit("scan_open_chatrooms", {"mode": mode}, priority=1)
    return jsonify({"ok": True, "task_id": task_id})


@app.route("/api/chatrooms/join", methods=["POST"])
def api_chatrooms_join():
    """오픈채팅 링크로 자동 입장."""
    body = request.get_json(silent=True) or {}
    links = body.get("links", [])

    if not links:
        return jsonify({"ok": False, "error": "links 필수"}), 400

    valid = [l.strip() for l in links if l.strip().startswith("https://open.kakao.com/")]
    if not valid:
        return jsonify({"ok": False, "error": "유효한 오픈채팅 링크 없음"}), 400
    if len(valid) > 20:
        return jsonify({"ok": False, "error": "최대 20개까지 가능"}), 400

    task_id = task_queue.submit(
        "join_open_chat", {"links": valid}, priority=TaskQueue.PRIORITY_URGENT
    )
    return jsonify({"ok": True, "task_id": task_id, "link_count": len(valid)})


# ---------------------------------------------------------------------------
# 라우트 — 카테고리 관리 API
# ---------------------------------------------------------------------------

@app.route("/api/categories")
def api_categories():
    """카테고리 목록."""
    data = _load_chatrooms()
    return jsonify({"categories": data.get("categories", [])})


@app.route("/api/categories", methods=["POST"])
def api_categories_add():
    """카테고리 추가."""
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "카테고리명 필수"}), 400

    data = _load_chatrooms()
    if name not in data.get("categories", []):
        data.setdefault("categories", []).append(name)
        _save_chatrooms(data)
    return jsonify({"ok": True})


@app.route("/api/categories/<path:name>", methods=["DELETE"])
def api_categories_delete(name):
    """카테고리 삭제."""
    data = _load_chatrooms()
    data["categories"] = [c for c in data.get("categories", []) if c != name]
    _save_chatrooms(data)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# 라우트 — 홍보 제어 API
# ---------------------------------------------------------------------------

@app.route("/api/promotion/start", methods=["POST"])
def api_promotion_start():
    """홍보 시작. 비밀번호 확인 필요."""
    global promotion_active, promoter

    body = request.get_json(silent=True) or {}
    password = str(body.get("password", "")).strip()
    if password != "4444":
        return jsonify({"ok": False, "error": "비밀번호가 올바르지 않습니다"}), 403

    promotion_active = True
    _update_config_promotion(True)
    logger.info("홍보 시작 (config.json 업데이트 완료)")

    # promoter가 아직 없으면 동적 초기화
    if promoter is None and controller is not None:
        try:
            from modules.open_chat_promoter import OpenChatPromoter
            promoter = OpenChatPromoter(controller, _load_config())
            logger.info("OpenChatPromoter 동적 초기화 완료")
        except Exception as pe:
            logger.warning("OpenChatPromoter 동적 초기화 실패: %s", pe)

    return jsonify({"ok": True})


@app.route("/api/promotion/stop", methods=["POST"])
def api_promotion_stop():
    """홍보 중지."""
    global promotion_active

    promotion_active = False
    _update_config_promotion(False)
    logger.info("홍보 중지 (config.json 업데이트 완료)")
    return jsonify({"ok": True})


@app.route("/api/promotion/history")
def api_promotion_history():
    """홍보 이력 (promotion_history.json)."""
    limit = request.args.get("limit", 20, type=int)
    history_path = PROJECT_ROOT / "data" / "promotion_history.json"
    try:
        if history_path.exists():
            raw = json.loads(history_path.read_text(encoding="utf-8"))
            # raw is {"campaign_id:room": "2026-02-23T17:00:00+09:00", ...}
            # Convert to list format
            items = []
            for key, ts in raw.items():
                parts = key.split(":", 1)
                items.append({
                    "campaign_id": parts[0] if len(parts) > 1 else key,
                    "room": parts[1] if len(parts) > 1 else "",
                    "time": ts,
                    "result": "성공",
                })
            # Sort by time desc
            items.sort(key=lambda x: x["time"], reverse=True)
            return jsonify({"history": items[:limit]})
    except Exception as e:
        logger.error("홍보 이력 로드 실패: %s", e)
    return jsonify({"history": []})


# ---------------------------------------------------------------------------
# 외부 연동 API (X-API-Key 인증)
# ---------------------------------------------------------------------------

@app.route("/api/rooms")
@require_api_key
def api_rooms():
    """오픈채팅방 목록 (외부용). enabled 상태인 방만 반환."""
    data = _load_chatrooms()
    rooms = []
    for r in data.get("rooms", []):
        if r.get("type", "open") == "open":
            rooms.append({
                "name": r["name"],
                "enabled": r.get("enabled", False),
                "categories": r.get("categories", []),
            })
    return jsonify({"ok": True, "rooms": rooms})


@app.route("/api/send", methods=["POST"])
@require_api_key
def api_send():
    """오픈채팅방 메시지 발송 (외부용).

    Body: {"room": "방이름", "message": "내용"}
    또는 여러 방: {"rooms": ["방1","방2"], "message": "내용"}
    """
    if not promotion_active:
        return jsonify({"ok": False, "error": "홍보가 중지 상태입니다. 먼저 홍보를 시작하세요."}), 403

    body = request.get_json(silent=True) or {}
    message = body.get("message", "").strip()
    if not message:
        return jsonify({"ok": False, "error": "message 필수"}), 400

    # 단일 방 또는 복수 방
    rooms = body.get("rooms", [])
    single = body.get("room", "").strip()
    if single and not rooms:
        rooms = [single]
    if not rooms:
        return jsonify({"ok": False, "error": "room 또는 rooms 필수"}), 400

    task_ids = []
    for room_name in rooms:
        room_name = room_name.strip()
        if not room_name:
            continue
        tid = task_queue.submit("promotion", {
            "room_name": room_name,
            "room_type": "open",
            "message": message,
        }, priority=TaskQueue.PRIORITY_LOW)
        task_ids.append({"room": room_name, "task_id": tid})

    return jsonify({"ok": True, "tasks": task_ids, "count": len(task_ids)})


# ---------------------------------------------------------------------------
# 태스크 큐 API (Railway -> 서버PC)
# ---------------------------------------------------------------------------

@app.route("/api/task/submit", methods=["POST"])
@require_api_key
def api_task_submit():
    """태스크 제출 (친구추가, 독촉, 안내 등)."""
    data = request.get_json(silent=True) or {}
    task_type = data.get("type")
    task_data = data.get("data", {})
    priority = data.get("priority", 2)

    if not task_type:
        return jsonify({"ok": False, "error": "type 필수"}), 400

    task_id = task_queue.submit(task_type, task_data, priority)
    if task_id is None:
        return jsonify({"ok": True, "task_id": None, "message": "중복 태스크 스킵"})

    return jsonify({"ok": True, "task_id": task_id})


@app.route("/api/task/status/<task_id>")
def api_task_status(task_id):
    """태스크 상태 조회."""
    task = task_queue.get_task(task_id)
    if not task:
        return jsonify({"ok": False, "error": "태스크 없음"}), 404
    return jsonify({"ok": True, "task": task})


@app.route("/api/task/<task_id>", methods=["DELETE"])
def api_task_delete(task_id):
    """태스크 삭제 (대기/실패만 가능, processing 불가)."""
    ok = task_queue.delete_task(task_id)
    if not ok:
        return jsonify({"ok": False, "error": "삭제 불가 (처리 중이거나 없음)"}), 400
    return jsonify({"ok": True})


@app.route("/api/task/cancel-campaign", methods=["POST"])
@require_api_key
def api_task_cancel_campaign():
    """특정 캠페인의 대기 중 홍보 태스크 일괄 취소."""
    body = request.get_json(silent=True) or {}
    campaign_id = body.get("campaign_id", "")
    if not campaign_id:
        return jsonify({"ok": False, "error": "campaign_id 필수"}), 400
    count = task_queue.cancel_campaign_tasks(campaign_id)
    return jsonify({"ok": True, "cancelled": count})


@app.route("/api/task/pending")
def api_task_pending():
    """대기 중 태스크 목록."""
    limit = request.args.get("limit", 50, type=int)
    tasks = task_queue.get_pending(limit)
    return jsonify({"ok": True, "tasks": tasks, "count": len(tasks)})


@app.route("/api/task/recent")
def api_task_recent():
    """최근 태스크 목록 (전체 상태)."""
    limit = request.args.get("limit", 20, type=int)
    tasks = task_queue.get_recent(limit)
    return jsonify({"ok": True, "tasks": tasks, "count": len(tasks)})


@app.route("/api/task/queue-status")
def api_task_queue_status():
    """태스크 큐 상태 (실행중/일시정지/대기건수)."""
    status = task_queue.get_queue_status()
    return jsonify({"ok": True, **status})


@app.route("/api/task/pause", methods=["POST"])
def api_task_pause():
    """태스크 큐 일시정지 — 현재 작업은 완료, 새 작업 중단."""
    task_queue.pause()
    return jsonify({"ok": True, "message": "태스크 큐 일시정지"})


@app.route("/api/task/resume", methods=["POST"])
def api_task_resume():
    """태스크 큐 재개."""
    task_queue.resume()
    return jsonify({"ok": True, "message": "태스크 큐 재개"})


@app.route("/api/task/emergency-stop", methods=["POST"])
def api_task_emergency_stop():
    """긴급 중지 — 대기 태스크 전부 취소 + 카카오톡 초기화."""
    cancelled = task_queue.emergency_stop()
    return jsonify({"ok": True, "message": f"긴급 중지: {cancelled}건 취소", "cancelled": cancelled})


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    cfg = _load_config()
    web_cfg = cfg.get("web", {})
    host = web_cfg.get("host", "0.0.0.0")
    port = web_cfg.get("port", 5000)

    # TaskQueue에 FriendManager 연결 + 워커 시작
    try:
        from modules.friend_manager import FriendManager
        fm = FriendManager()
        task_queue.set_friend_manager(fm)
        task_queue.start_worker()
        logger.info("태스크 큐 워커 시작 완료")
    except Exception as e:
        logger.warning("FriendManager 초기화 실패 (태스크 큐 비활성): %s", e)

    # KakaoReset 연결 (태스크 전/후 카카오톡 초기화)
    try:
        from modules.kakao_reset import KakaoReset
        kakao_reset = KakaoReset()
        task_queue.set_kakao_reset(kakao_reset)
        logger.info("KakaoReset 연결 완료")
    except Exception as e:
        logger.warning("KakaoReset 초기화 실패: %s", e)

    # 시스템 자동 시작 (KakaoController + Promoter)
    try:
        from modules.kakao_controller import KakaoController
        controller = KakaoController()
        if controller.find_kakao_window():
            monitoring_active = True
            start_time = now_kst()

            # Promoter 초기화
            promo_config = cfg.get("promotion", {})
            if promo_config.get("enabled", False):
                try:
                    from modules.open_chat_promoter import OpenChatPromoter
                    promoter = OpenChatPromoter(controller, cfg)
                    task_queue.set_promoter(promoter)
                    promotion_active = True

                    # 홍보 전용 백그라운드 스레드 시작
                    _promotion_stop_event.clear()
                    _promotion_thread = threading.Thread(
                        target=_promotion_loop, daemon=True, name="PromotionLoop"
                    )
                    _promotion_thread.start()
                    logger.info("OpenChatPromoter + 홍보 루프 자동 시작 완료")
                except Exception as pe:
                    logger.warning("OpenChatPromoter 초기화 실패: %s", pe)

            logger.info("시스템 자동 시작 완료 (카카오톡 연결됨)")
        else:
            logger.warning("카카오톡 윈도우 못 찾음 — 수동 시작 필요")
    except Exception as e:
        logger.warning("시스템 자동 시작 실패: %s", e)

    print("=" * 50)
    print("  카카오톡 홍보+발신 전용 대시보드")
    print(f"  http://{host}:{port}")
    print("  태스크 큐: 활성")
    print("  모드: 홍보 + 알림 발신 전용 (채팅 감지 없음)")
    print("  종료: Ctrl+C")
    print("=" * 50)

    app.run(host=host, port=port, debug=False, threaded=True)

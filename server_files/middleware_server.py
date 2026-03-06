"""
middleware_server.py - 멀티 PC 카카오톡 태스크 라우팅 미들웨어

Railway → 미들웨어 → 담당 PC 라우팅.
PC → 미들웨어 → Railway 콜백 프록시.

Usage:
    pip install flask requests
    python middleware_server.py
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from flask import Flask, jsonify, request, render_template_string

# ─── 설정 ───

KST = timezone(timedelta(hours=9))
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "middleware_config.json"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

app = Flask(__name__)

# 로깅
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOG_DIR / "middleware.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("middleware")

# ─── Config ───

_config = {}
_config_lock = threading.Lock()


def _load_config() -> dict:
    """config.json 로드"""
    global _config
    if not CONFIG_PATH.exists():
        logger.warning("설정 파일 없음: %s → 기본값 생성", CONFIG_PATH)
        default = {
            "pcs": {
                "pc1": {
                    "name": "카비서PC (친구추가/알림)",
                    "url": "http://222.122.194.202:5050",
                    "roles": ["friend_add", "reminder", "notification", "test_send"],
                    "enabled": True,
                },
            },
            "railway_url": "https://web-production-1776e.up.railway.app",
            "api_key": "_fNmY5SeHyigMgkR5LIngpxBB1gDoZLF",
        }
        CONFIG_PATH.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
        _config = default
        return _config

    with open(CONFIG_PATH, encoding="utf-8") as f:
        _config = json.loads(f.read())
    return _config


def _save_config():
    """현재 _config를 파일에 저장"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(_config, f, ensure_ascii=False, indent=2)


def _get_config() -> dict:
    with _config_lock:
        return _config


# ─── API Key 인증 ───

def _check_api_key():
    """X-API-Key 헤더 검증"""
    cfg = _get_config()
    expected = cfg.get("api_key", "")
    if not expected:
        return True
    provided = request.headers.get("X-API-Key", "")
    return provided == expected


# ─── PC 라우팅 ───

# 홍보 발송 기록: { "방이름": { "pc_id": datetime } }
_promotion_log: dict = {}

# 홍보 전용 역할 목록 (이 역할들은 전용 PC 우선)
_PROMO_ROLES = {"promotion", "join_open_chat", "scan_open_chatrooms"}


def _find_pc_for_type(task_type: str) -> tuple:
    """task_type을 roles에 포함한 enabled+healthy PC 반환.
    공유 역할(홍보 관련)은 전용 PC 우선, PC1 백업.
    Returns: (pc_id, pc_info) or (None, None)
    """
    cfg = _get_config()
    pcs = cfg.get("pcs", {})
    candidates = []
    for pc_id, pc in pcs.items():
        if not pc.get("enabled", True):
            continue
        if not pc.get("healthy", True):
            continue
        if task_type in pc.get("roles", []):
            candidates.append((pc_id, pc))

    if not candidates:
        return None, None
    if len(candidates) == 1:
        return candidates[0]

    # 공유 역할: 전용 PC(역할이 홍보 관련만 있는 PC) 우선
    if task_type in _PROMO_ROLES:
        for pc_id, pc in candidates:
            roles = set(pc.get("roles", []))
            if roles.issubset(_PROMO_ROLES):
                return pc_id, pc
    return candidates[0]


def _find_pc_for_promotion(room_name: str) -> tuple:
    """홍보 태스크용 PC 선택 — 방별 쿨다운 기반 분배.
    Returns: (pc_id, pc_info) or (None, None)
    """
    cfg = _get_config()
    cooldown_min = cfg.get("promotion_cooldown_minutes", 30)
    pcs = cfg.get("pcs", {})
    now = datetime.now(KST)

    # 1. promotion 역할 + enabled + healthy 후보
    candidates = []
    for pc_id, pc in pcs.items():
        if not pc.get("enabled", True) or not pc.get("healthy", True):
            continue
        if "promotion" in pc.get("roles", []):
            candidates.append((pc_id, pc))

    if not candidates:
        return None, None
    if len(candidates) == 1:
        return candidates[0]

    # 2. 방별 쿨다운 확인
    room_log = _promotion_log.get(room_name, {})
    available = []   # 쿨다운 만료된 PC
    in_cooldown = []  # 쿨다운 중인 PC

    for pc_id, pc in candidates:
        last_send = room_log.get(pc_id)
        if last_send is None:
            # 한 번도 안 보낸 PC → 최우선
            available.append((pc_id, pc, datetime.min.replace(tzinfo=KST)))
        else:
            elapsed = (now - last_send).total_seconds()
            if elapsed >= cooldown_min * 60:
                available.append((pc_id, pc, last_send))
            else:
                in_cooldown.append((pc_id, pc, last_send))

    # 3. 쿨다운 만료된 PC 중 가장 오래전에 보낸 PC (번갈아가기 효과)
    if available:
        available.sort(key=lambda x: x[2])
        chosen = available[0]
        logger.info("홍보 라우팅 [%s] → %s (쿨다운 만료)", room_name, chosen[0])
        return chosen[0], chosen[1]

    # 4. 모두 쿨다운 중 → 가장 먼저 만료되는 PC
    in_cooldown.sort(key=lambda x: x[2])
    chosen = in_cooldown[0]
    logger.info("홍보 라우팅 [%s] → %s (쿨다운 중이나 가장 오래됨)", room_name, chosen[0])
    return chosen[0], chosen[1]


def _record_promotion(room_name: str, pc_id: str):
    """홍보 발송 기록 저장"""
    if room_name not in _promotion_log:
        _promotion_log[room_name] = {}
    _promotion_log[room_name][pc_id] = datetime.now(KST)


def _cleanup_promotion_log():
    """쿨다운 × 2 지난 기록 삭제 (메모리 관리)"""
    cfg = _get_config()
    cutoff_sec = cfg.get("promotion_cooldown_minutes", 30) * 60 * 2
    now = datetime.now(KST)
    expired_rooms = []
    for room_name, pcs in _promotion_log.items():
        expired_pcs = [pc_id for pc_id, ts in pcs.items() if (now - ts).total_seconds() > cutoff_sec]
        for pc_id in expired_pcs:
            del pcs[pc_id]
        if not pcs:
            expired_rooms.append(room_name)
    for room_name in expired_rooms:
        del _promotion_log[room_name]


def _find_pcs_with_role(role: str) -> list:
    """특정 role을 가진 모든 enabled PC 목록"""
    cfg = _get_config()
    pcs = cfg.get("pcs", {})
    result = []
    for pc_id, pc in pcs.items():
        if pc.get("enabled", True) and role in pc.get("roles", []):
            result.append((pc_id, pc))
    return result


# ─── 대시보드 HTML ───

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>카비서 미들웨어</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Noto Sans KR',sans-serif;background:#f5f5f5;color:#333;font-size:14px}
.mw-header{background:#3c1e1e;color:#fff;padding:14px 20px;display:flex;align-items:center;justify-content:space-between}
.mw-header h1{font-size:20px;font-weight:700;color:#fee500}
.mw-header .refresh-info{font-size:12px;color:rgba(255,255,255,.6)}
.mw-container{max-width:1100px;margin:0 auto;padding:24px 16px}
.section-title{font-size:16px;font-weight:700;margin:28px 0 12px;color:#3c1e1e;display:flex;align-items:center;gap:8px}
.section-title:first-child{margin-top:0}

.pc-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px}
.pc-card{background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 4px rgba(0,0,0,.06);border:2px solid transparent;transition:border-color .2s}
.pc-card.healthy{border-color:#dcfce7}
.pc-card.unhealthy{border-color:#fecaca}
.pc-card.disabled{border-color:#e5e7eb;opacity:.7}
.pc-card-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px}
.pc-name{font-size:15px;font-weight:700;color:#3c1e1e}
.pc-url{font-size:12px;color:#888;margin-top:2px;font-family:monospace}
.pc-controls{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}
.toggle-wrap{position:relative;width:44px;height:24px;flex-shrink:0}
.toggle-wrap input{opacity:0;width:0;height:0}
.toggle-slider{position:absolute;cursor:pointer;inset:0;background:#ccc;border-radius:24px;transition:.3s}
.toggle-slider:before{content:"";position:absolute;height:18px;width:18px;left:3px;bottom:3px;background:#fff;border-radius:50%;transition:.3s}
.toggle-wrap input:checked+.toggle-slider{background:#22c55e}
.toggle-wrap input:checked+.toggle-slider:before{transform:translateX(20px)}
.pc-roles{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:12px}
.role-tag{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;background:#f3f4f6;color:#555}
.pc-stats{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:8px}
.health-badge{display:inline-block;padding:3px 10px;border-radius:10px;font-size:12px;font-weight:600}
.health-badge.ok{background:#dcfce7;color:#166534}
.health-badge.fail{background:#fef2f2;color:#991b1b}
.health-badge.off{background:#f3f4f6;color:#9ca3af}
.queue-info{font-size:13px;color:#555}
.queue-info b{color:#3c1e1e}
.fail-count{font-size:12px;color:#dc2626;font-weight:600}
.pc-footer{font-size:11px;color:#aaa;margin-top:4px}

.form-card{background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 4px rgba(0,0,0,.06)}
.form-row{display:flex;gap:12px;margin-bottom:12px}
.form-row>*{flex:1}
.form-group{margin-bottom:12px}
.form-group label{display:block;font-size:13px;font-weight:600;color:#555;margin-bottom:4px}
.form-group select,.form-group input,.form-group textarea{width:100%;padding:8px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:14px;font-family:inherit}
.form-group textarea{font-family:'Consolas','Monaco',monospace;font-size:13px;resize:vertical}
.form-group select:focus,.form-group input:focus,.form-group textarea:focus{outline:none;border-color:#fee500;box-shadow:0 0 0 2px rgba(254,229,0,.2)}
.btn{display:inline-block;padding:8px 16px;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;transition:opacity .2s}
.btn:hover{opacity:.85}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn-primary{background:#fee500;color:#3c1e1e}
.btn-danger{background:#ef4444;color:#fff}
.btn-success{background:#22c55e;color:#fff}
.btn-secondary{background:#6b7280;color:#fff}
.btn-sm{padding:5px 12px;font-size:12px}
.result-area{margin-top:12px;padding:12px 16px;border-radius:8px;font-size:13px;font-family:'Consolas',monospace;white-space:pre-wrap;word-break:break-all;display:none}
.result-area.show{display:block}
.result-area.success{background:#f0fdf4;color:#166534;border:1px solid #bbf7d0}
.result-area.error{background:#fef2f2;color:#991b1b;border:1px solid #fecaca}
.result-area.info{background:#eff6ff;color:#1e40af;border:1px solid #bfdbfe}
.lookup-row{display:flex;gap:8px}
.lookup-row input{flex:1}

/* Tabs */
.tab-bar{display:flex;gap:4px;margin-bottom:16px;border-bottom:2px solid #e5e7eb;padding-bottom:0}
.tab-btn{padding:8px 16px;border:none;background:none;font-size:14px;font-weight:600;color:#888;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-2px;transition:.2s}
.tab-btn.active{color:#3c1e1e;border-bottom-color:#fee500}
.tab-btn:hover{color:#3c1e1e}

/* Task Table */
.task-table{width:100%;border-collapse:collapse;font-size:13px}
.task-table th{text-align:left;padding:8px 10px;background:#f8f9fa;color:#555;font-weight:600;border-bottom:1px solid #e5e7eb}
.task-table td{padding:8px 10px;border-bottom:1px solid #f3f4f6;vertical-align:top}
.task-table tr:hover{background:#fafafa}
.status-badge{display:inline-block;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:600}
.status-badge.done{background:#dcfce7;color:#166534}
.status-badge.failed{background:#fef2f2;color:#991b1b}
.status-badge.processing{background:#fef3c7;color:#92400e}
.status-badge.pending{background:#e0e7ff;color:#3730a3}
.task-data{max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;color:#666}
.task-result{max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}

/* Chatroom Table */
.room-table{width:100%;border-collapse:collapse;font-size:13px}
.room-table th{text-align:left;padding:8px 10px;background:#f8f9fa;color:#555;font-weight:600;border-bottom:1px solid #e5e7eb}
.room-table td{padding:8px 10px;border-bottom:1px solid #f3f4f6}

.empty-msg{text-align:center;color:#aaa;padding:30px;font-size:14px}
.toast{position:fixed;bottom:24px;right:24px;background:#3c1e1e;color:#fff;padding:12px 20px;border-radius:8px;font-size:14px;opacity:0;transition:opacity .3s;pointer-events:none;z-index:100}
.toast.show{opacity:1}

@media(max-width:600px){
.pc-grid{grid-template-columns:1fr}
.form-row{flex-direction:column;gap:0}
.task-table{font-size:12px}
}
</style>
</head>
<body>

<header class="mw-header">
    <h1>카비서 미들웨어</h1>
    <span class="refresh-info" id="refreshInfo">자동갱신 10초</span>
</header>

<main class="mw-container">

    <h2 class="section-title" style="margin-top:0">PC 상태</h2>
    <div class="pc-grid" id="pcGrid"><div class="empty-msg">로딩 중...</div></div>

    <h2 class="section-title">최근 태스크</h2>
    <div class="form-card">
        <div class="tab-bar" id="taskTabs"></div>
        <div style="overflow-x:auto">
            <table class="task-table" id="taskTable">
                <thead><tr><th>유형</th><th>대상</th><th>내용</th><th>상태</th><th>결과</th><th>시간</th><th></th></tr></thead>
                <tbody id="taskBody"><tr><td colspan="7" class="empty-msg">PC를 선택하세요</td></tr></tbody>
            </table>
        </div>
    </div>

    <h2 class="section-title">채팅방 목록</h2>
    <div class="form-card">
        <div class="tab-bar" id="roomTabs"></div>
        <div id="bulkBar" style="display:none;padding:8px 12px;margin-bottom:8px;background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;align-items:center;gap:8px;flex-wrap:wrap">
            <span id="bulkCount" style="font-size:13px;font-weight:600;color:#0369a1">0개 선택</span>
            <select id="bulkType" style="padding:3px 8px;border:1px solid #ddd;border-radius:4px;font-size:12px">
                <option value="">유형 변경</option><option value="open">오픈</option><option value="normal">일반</option>
            </select>
            <button class="btn btn-sm btn-primary" onclick="bulkChangeType()" style="font-size:12px">유형 적용</button>
            <span style="color:#ddd">|</span>
            <span style="font-size:12px;color:#666">카테고리:</span>
            <button class="btn btn-sm" onclick="bulkAddCat('체험단')" style="font-size:11px;padding:2px 8px;background:#dcfce7;border:1px solid #22c55e;color:#166534;border-radius:12px">+체험단</button>
            <button class="btn btn-sm" onclick="bulkAddCat('리뷰-실')" style="font-size:11px;padding:2px 8px;background:#dcfce7;border:1px solid #22c55e;color:#166534;border-radius:12px">+리뷰-실</button>
            <button class="btn btn-sm" onclick="bulkAddCat('리뷰-빈')" style="font-size:11px;padding:2px 8px;background:#dcfce7;border:1px solid #22c55e;color:#166534;border-radius:12px">+리뷰-빈</button>
            <button class="btn btn-sm" onclick="bulkAddCat('마케팅홍보')" style="font-size:11px;padding:2px 8px;background:#dcfce7;border:1px solid #22c55e;color:#166534;border-radius:12px">+마케팅홍보</button>
            <span style="color:#ddd">|</span>
            <button class="btn btn-sm" onclick="bulkToggleEnabled(true)" style="font-size:12px;background:#dcfce7;border:1px solid #22c55e;color:#166534">일괄 활성</button>
            <button class="btn btn-sm" onclick="bulkToggleEnabled(false)" style="font-size:12px;background:#fee2e2;border:1px solid #f87171;color:#991b1b">일괄 비활성</button>
            <button class="btn btn-sm btn-danger" onclick="bulkDelete()" style="font-size:12px">일괄 삭제</button>
        </div>
        <div style="overflow-x:auto">
            <table class="room-table" id="roomTable">
                <thead><tr><th style="width:30px"><input type="checkbox" id="checkAll" onchange="toggleCheckAll(this)"></th><th>이름</th><th>유형</th><th>카테고리</th><th></th></tr></thead>
                <tbody id="roomBody"><tr><td colspan="5" class="empty-msg">PC를 선택하세요</td></tr></tbody>
            </table>
        </div>
        <div style="margin-top:12px" id="roomControls"></div>
    </div>

    <h2 class="section-title">홍보 쿨다운</h2>
    <div class="form-card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px">
            <span style="font-size:13px;color:#666" id="cooldownInfo">불러오는 중...</span>
            <div style="display:flex;gap:6px;align-items:center">
                <span id="promoStatus" style="font-size:12px;font-weight:600;padding:3px 10px;border-radius:12px;background:#f3f4f6;color:#666">확인중</span>
                <button class="btn btn-sm" onclick="togglePromotion(true)" id="promoStartBtn" style="font-size:12px;background:#dcfce7;border:1px solid #22c55e;color:#166534">홍보 시작</button>
                <button class="btn btn-sm" onclick="togglePromotion(false)" id="promoStopBtn" style="font-size:12px;background:#fee2e2;border:1px solid #f87171;color:#991b1b">홍보 중지</button>
                <button class="btn btn-sm" onclick="loadCooldown()" style="font-size:12px">새로고침</button>
            </div>
        </div>
        <div id="cooldownList" style="display:flex;flex-direction:column;gap:6px"></div>
    </div>

    <h2 class="section-title">태스크 전송</h2>
    <div class="form-card">
        <div class="form-group">
            <label>유형</label>
            <select id="taskType" onchange="updateFields()">
                <option value="notification">안내 메시지</option>
                <option value="friend_add">친구추가</option>
                <option value="reminder">독촉 메시지</option>
                <option value="test_send">테스트 발송</option>
                <option value="promotion">홍보 메시지</option>
                <option value="join_open_chat">오픈채팅 참여</option>
                <option value="scan_open_chatrooms">채팅방 스캔</option>
            </select>
        </div>
        <div id="taskFields"></div>
        <button class="btn btn-primary" id="submitBtn" onclick="submitTask()">전송</button>
        <div class="result-area" id="submitResult"></div>
    </div>

    <h2 class="section-title">태스크 조회</h2>
    <div class="form-card">
        <div class="lookup-row">
            <input type="text" id="taskIdInput" placeholder="태스크 ID 입력">
            <button class="btn btn-primary btn-sm" onclick="checkTask()">조회</button>
        </div>
        <div class="result-area" id="statusResult"></div>
    </div>

</main>

<div class="toast" id="toast"></div>

<script>
var API_KEY = '""" + "{{ api_key }}" + r"""';
var HEADERS = {'Content-Type':'application/json','X-API-Key':API_KEY};
var pcIds = [];
var activeTaskPC = '';
var activeRoomPC = '';

var TYPE_LABELS = {notification:'안내 메시지',friend_add:'친구추가',reminder:'독촉 메시지',test_send:'테스트 발송',promotion:'홍보 메시지',join_open_chat:'오픈채팅 참여',scan_open_chatrooms:'채팅방 스캔'};
var TYPE_FIELDS = {
    notification:[{key:'name',label:'이름',ph:'예: 홍길동'},{key:'phone',label:'전화번호',ph:'예: 010-1234-5678'},{key:'message',label:'메시지',ta:1,ph:'보낼 메시지 입력'}],
    friend_add:[{key:'name',label:'이름',ph:'예: 홍길동'},{key:'phone',label:'전화번호',ph:'예: 010-1234-5678'}],
    reminder:[{key:'name',label:'이름',ph:'예: 홍길동'},{key:'message',label:'메시지',ta:1,ph:'독촉 메시지 입력'}],
    test_send:[{key:'room_name',label:'채팅방 이름',ph:'채팅방 이름 입력'},{key:'message',label:'메시지',ta:1,ph:'테스트 메시지'},{key:'room_type',label:'채팅방 유형',sel:[['open','오픈채팅'],['normal','일반채팅']]}],
    promotion:[{key:'room_name',label:'채팅방 이름',ph:'채팅방 이름 입력'},{key:'message',label:'메시지',ta:1,ph:'홍보 메시지'},{key:'room_type',label:'채팅방 유형',sel:[['open','오픈채팅'],['normal','일반채팅']]},{key:'campaign_id',label:'캠페인 ID',ph:'(선택사항)'}],
    join_open_chat:[{key:'links',label:'오픈채팅 링크',ta:1,ph:'링크를 한 줄에 하나씩 입력'}],
    scan_open_chatrooms:[]
};
function updateFields() {
    var type = document.getElementById('taskType').value;
    var fields = TYPE_FIELDS[type] || [];
    var c = document.getElementById('taskFields');
    if (!fields.length) { c.innerHTML = '<div style="color:#888;padding:8px 0;font-size:13px">추가 입력 없음 (자동 실행)</div>'; return; }
    var html = '';
    fields.forEach(function(f){
        html += '<div class="form-group"><label>'+f.label+'</label>';
        if (f.sel) {
            html += '<select id="field_'+f.key+'">';
            f.sel.forEach(function(o){ html += '<option value="'+o[0]+'">'+o[1]+'</option>'; });
            html += '</select>';
        } else if (f.ta) {
            html += '<textarea id="field_'+f.key+'" rows="3" placeholder="'+(f.ph||'')+'"></textarea>';
        } else {
            html += '<input type="text" id="field_'+f.key+'" placeholder="'+(f.ph||'')+'">';
        }
        html += '</div>';
    });
    c.innerHTML = html;
}
function statusLabel(s) { var m = {done:'완료',failed:'실패',processing:'처리중',pending:'대기중'}; return m[s]||s; }
function extractInfo(t) {
    var d = t.data||{}, target = d.name||d.room_name||'', content = d.message||'';
    if (d.phone) target += ' ('+d.phone+')';
    if (d.links) content = d.links.length+'개 링크';
    if (t.type==='scan_open_chatrooms') content = '채팅방 스캔';
    return {target:target, content:content};
}
function extractResult(t) {
    if (!t.result) return '-';
    try { var r = JSON.parse(t.result); if (r.ok||r.success) return r.message||'성공'; if (r.error) return '실패: '+r.error; return r.message||'완료'; }
    catch(e) { return t.result.substring(0,50); }
}
function showResult(el, text, cls) { el.textContent = text; el.className = 'result-area show ' + cls; }
function showToast(msg) {
    var t = document.getElementById('toast'); t.textContent = msg; t.className = 'toast show';
    setTimeout(function(){ t.className = 'toast'; }, 2500);
}
function formatTime(iso) {
    if (!iso) return '-';
    var d = new Date(iso);
    var opts = {timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false};
    var parts = new Intl.DateTimeFormat('ko-KR',opts).formatToParts(d);
    var v = {}; parts.forEach(function(p){v[p.type]=p.value;});
    return v.month+'/'+v.day+' '+v.hour+':'+v.minute+':'+v.second;
}
function esc(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

/* ── PC Cards ── */
function renderPCs(data) {
    var grid = document.getElementById('pcGrid');
    var pcs = data.pcs || {};
    var keys = Object.keys(pcs);
    pcIds = keys;
    if (!keys.length) { grid.innerHTML = '<div class="empty-msg">등록된 PC 없음</div>'; return; }
    var html = '';
    keys.forEach(function(id) {
        var pc = pcs[id];
        var enabled = pc.enabled !== false, healthy = pc.healthy !== false;
        var cls = !enabled ? 'disabled' : (healthy ? 'healthy' : 'unhealthy');
        var bCls = !enabled ? 'off' : (healthy ? 'ok' : 'fail');
        var bTxt = !enabled ? '비활성' : (healthy ? '정상' : '장애');
        var qs = pc.queue_status || {}, pend = qs.pending_count||0, proc = qs.processing_count||0;
        var paused = qs.paused || false;
        html += '<div class="pc-card '+cls+'">';
        html += '<div class="pc-card-header"><div>';
        html += '<div class="pc-name">'+esc(pc.name)+'</div>';
        html += '<div class="pc-url">'+esc(pc.url)+'</div>';
        html += '</div>';
        html += '<label class="toggle-wrap"><input type="checkbox" '+(enabled?'checked':'')+' onchange="togglePC(this,&quot;'+id+'&quot;)"><span class="toggle-slider"></span></label>';
        html += '</div>';
        html += '<div class="pc-roles">';
        (pc.roles||[]).forEach(function(r){ html += '<span class="role-tag">'+r+'</span>'; });
        html += '</div>';
        html += '<div class="pc-stats">';
        html += '<span class="health-badge '+bCls+'">'+bTxt+'</span>';
        html += '<span class="queue-info">대기 <b>'+pend+'</b></span>';
        html += '<span class="queue-info">처리중 <b>'+proc+'</b></span>';
        if ((pc.fail_count||0) > 0) html += '<span class="fail-count">실패 '+pc.fail_count+'회</span>';
        html += '</div>';
        html += '<div class="pc-controls">';
        html += '<button class="btn btn-sm '+(paused?'btn-success':'btn-secondary')+'" onclick="queueControl(&quot;'+id+'&quot;,&quot;'+(paused?'resume':'pause')+'&quot;)">'+(paused?'큐 재개':'큐 일시정지')+'</button> ';
        if (pend > 0) html += '<button class="btn btn-sm btn-danger" onclick="clearQueue(&quot;'+id+'&quot;,'+pend+')">대기 '+pend+'건 클리어</button>';
        html += '</div>';
        html += '<div class="pc-footer">체크: '+formatTime(pc.last_check)+'</div>';
        html += '</div>';
    });
    grid.innerHTML = html;
    buildTabs();
}

/* ── Tabs ── */
function selectTaskPC(id) { activeTaskPC = id; loadTasks(id); }
function selectRoomPC(id) { activeRoomPC = id; loadRooms(id); }
function buildTabs() {
    buildTabBar('taskTabs', activeTaskPC, 'selectTaskPC');
    buildTabBar('roomTabs', activeRoomPC, 'selectRoomPC');
    if (!activeTaskPC && pcIds.length) { activeTaskPC = pcIds[0]; loadTasks(pcIds[0]); }
    if (!activeRoomPC && pcIds.length) { activeRoomPC = pcIds[0]; loadRooms(pcIds[0]); }
}
function buildTabBar(elId, activeId, fnName) {
    var bar = document.getElementById(elId);
    var html = '';
    pcIds.forEach(function(id) {
        html += '<button class="tab-btn '+(id===activeId?'active':'')+'" onclick="'+fnName+'(&quot;'+id+'&quot;)">'+id+'</button>';
    });
    bar.innerHTML = html;
}
function setActiveTab(barId, activeId) {
    var btns = document.getElementById(barId).querySelectorAll('.tab-btn');
    btns.forEach(function(b){ b.className = 'tab-btn' + (b.textContent===activeId?' active':''); });
}

/* ── Tasks ── */
function loadTasks(pcId) {
    activeTaskPC = pcId;
    setActiveTab('taskTabs', pcId);
    var tbody = document.getElementById('taskBody');
    tbody.innerHTML = '<tr><td colspan="7" class="empty-msg">로딩 중...</td></tr>';
    fetch('/api/proxy/'+pcId+'/task/recent')
    .then(function(r){ return r.json(); })
    .then(function(d){
        if (!d.ok || !d.tasks || !d.tasks.length) { tbody.innerHTML = '<tr><td colspan="7" class="empty-msg">태스크 없음</td></tr>'; return; }
        var html = '';
        d.tasks.forEach(function(t){
            var info = extractInfo(t);
            var res = extractResult(t);
            html += '<tr>';
            html += '<td>'+esc(TYPE_LABELS[t.type]||t.type)+'</td>';
            html += '<td>'+esc(info.target||'-')+'</td>';
            html += '<td class="task-data" title="'+esc(info.content)+'">'+esc((info.content||'-').substring(0,40))+'</td>';
            html += '<td><span class="status-badge '+(t.status||'')+'">'+statusLabel(t.status)+'</span></td>';
            html += '<td class="task-result" title="'+esc(res)+'">'+esc(res.substring(0,40))+'</td>';
            html += '<td style="white-space:nowrap">'+formatTime(t.created_at)+'</td>';
            html += '<td>';
            if (t.status==='pending'||t.status==='failed') html += '<button class="btn btn-danger btn-sm" onclick="deleteTask(&quot;'+pcId+'&quot;,&quot;'+t.id+'&quot;)">삭제</button>';
            html += '</td>';
            html += '</tr>';
        });
        tbody.innerHTML = html;
    })
    .catch(function(){ tbody.innerHTML = '<tr><td colspan="7" class="empty-msg">연결 실패</td></tr>'; });
}
function deleteTask(pcId, taskId) {
    if (!confirm('태스크 '+taskId+' 삭제?')) return;
    fetch('/api/proxy/'+pcId+'/task/'+taskId, {method:'DELETE',headers:HEADERS})
    .then(function(r){ return r.json(); })
    .then(function(d){ showToast(d.ok?'삭제 완료':'오류: '+(d.error||'')); loadTasks(pcId); })
    .catch(function(){ showToast('통신 오류'); });
}

/* ── Chatrooms ── */
function loadRooms(pcId) {
    activeRoomPC = pcId;
    setActiveTab('roomTabs', pcId);
    var tbody = document.getElementById('roomBody');
    tbody.innerHTML = '<tr><td colspan="5" class="empty-msg">로딩 중...</td></tr>';
    document.getElementById('checkAll').checked = false;
    updateBulkBar();
    fetch('/api/proxy/'+pcId+'/chatrooms')
    .then(function(r){ return r.json(); })
    .then(function(d){
        var rooms = d.rooms || [];
        if (!rooms.length) { tbody.innerHTML = '<tr><td colspan="5" class="empty-msg">채팅방 없음</td></tr>'; updateRoomControls(pcId); return; }
        var html = '';
        rooms.forEach(function(rm){
            var rn = esc(rm.name).replace(/'/g,'&#39;');
            html += '<tr>';
            var enb = rm.enabled !== false;
            html += '<td><input type="checkbox" class="room-chk" data-name="'+rn+'" onchange="updateBulkBar()"></td>';
            html += '<td style="opacity:'+(enb?'1':'0.4')+'">'+esc(rm.name)+'</td>';
            html += '<td><select onchange="updateRoomType(\''+pcId+'\',\''+rn+'\',this.value)" style="padding:2px 4px;border:1px solid #ddd;border-radius:4px;font-size:12px">';
            html += '<option value="open"'+(rm.type!=='normal'?' selected':'')+'>오픈</option>';
            html += '<option value="normal"'+(rm.type==='normal'?' selected':'')+'>일반</option>';
            html += '</select></td>';
            var cats = rm.categories||[];
            var ALL_CATS = ['체험단','리뷰-실','리뷰-빈','마케팅홍보'];
            html += '<td style="display:flex;flex-wrap:wrap;gap:3px">';
            ALL_CATS.forEach(function(cat){
                var on = cats.indexOf(cat)>=0;
                html += '<span onclick="toggleCat(\''+pcId+'\',\''+rn+'\',\''+cat+'\',this)" style="cursor:pointer;padding:1px 6px;border-radius:8px;font-size:11px;border:1px solid '+(on?'#22c55e':'#ddd')+';background:'+(on?'#dcfce7':'#f9fafb')+';color:'+(on?'#166534':'#999')+'">'+cat+'</span>';
            });
            html += '</td>';
            html += '<td style="white-space:nowrap">';
            html += '<label style="cursor:pointer;margin-right:6px"><input type="checkbox" '+(enb?'checked':'')+' onchange="toggleRoomEnabled(\''+pcId+'\',\''+rn+'\',this.checked)" style="margin-right:2px">'+(enb?'활성':'비활성')+'</label>';
            html += '<button class="btn btn-sm btn-primary" onclick="testSend(\''+pcId+'\',\''+rn+'\')">테스트</button> ';
            html += '<button class="btn btn-sm btn-danger" onclick="deleteRoom(\''+pcId+'\',\''+rn+'\')">삭제</button>';
            html += '</td>';
            html += '</tr>';
        });
        tbody.innerHTML = html;
        updateRoomControls(pcId);
    })
    .catch(function(){ tbody.innerHTML = '<tr><td colspan="5" class="empty-msg">연결 실패</td></tr>'; });
}
function toggleCheckAll(el) {
    var chks = document.querySelectorAll('.room-chk');
    for (var i=0;i<chks.length;i++) chks[i].checked = el.checked;
    updateBulkBar();
}
function getCheckedRooms() {
    var chks = document.querySelectorAll('.room-chk:checked');
    var names = [];
    for (var i=0;i<chks.length;i++) names.push(chks[i].getAttribute('data-name').replace(/&#39;/g,"'"));
    return names;
}
function updateBulkBar() {
    var names = getCheckedRooms();
    var bar = document.getElementById('bulkBar');
    if (names.length > 0) {
        bar.style.display = 'flex';
        document.getElementById('bulkCount').textContent = names.length+'개 선택';
    } else {
        bar.style.display = 'none';
    }
}
function bulkChangeType() {
    var names = getCheckedRooms();
    var newType = document.getElementById('bulkType').value;
    if (!newType) { showToast('유형을 선택하세요'); return; }
    if (!names.length) return;
    var label = newType==='normal'?'일반':'오픈';
    showToast(names.length+'개 → '+label+' 변경 중...');
    var done = 0;
    names.forEach(function(name){
        fetch('/api/proxy/'+activeRoomPC+'/chatrooms/'+encodeURIComponent(name), {method:'PUT',headers:HEADERS,body:JSON.stringify({type:newType})})
        .then(function(r){ return r.json(); })
        .then(function(){ done++; if(done===names.length){ showToast(done+'개 유형 변경 완료'); loadRooms(activeRoomPC); } })
        .catch(function(){ done++; });
    });
}
function bulkAddCat(cat) {
    var names = getCheckedRooms();
    if (!names.length) return;
    var rows = document.querySelectorAll('.room-chk:checked');
    var done = 0;
    showToast(names.length+'개에 '+cat+' 추가 중...');
    rows.forEach(function(chk){
        var tr = chk.closest('tr');
        var spans = tr.querySelectorAll('td:nth-child(4) span');
        var cats = [];
        for (var i=0;i<spans.length;i++){
            if (spans[i].style.borderColor==='rgb(34, 197, 94)') cats.push(spans[i].textContent);
        }
        if (cats.indexOf(cat)<0) cats.push(cat);
        var name = chk.getAttribute('data-name').replace(/&#39;/g,"'");
        fetch('/api/proxy/'+activeRoomPC+'/chatrooms/'+encodeURIComponent(name), {method:'PUT',headers:HEADERS,body:JSON.stringify({categories:cats})})
        .then(function(r){ return r.json(); })
        .then(function(){ done++; if(done===names.length){ showToast(done+'개에 '+cat+' 추가 완료'); loadRooms(activeRoomPC); } })
        .catch(function(){ done++; });
    });
}
function bulkToggleEnabled(enabled) {
    var names = getCheckedRooms();
    if (!names.length) return;
    var done = 0;
    showToast(names.length+'개 '+(enabled?'활성':'비활성')+' 변경 중...');
    names.forEach(function(name){
        fetch('/api/proxy/'+activeRoomPC+'/chatrooms/'+encodeURIComponent(name), {method:'PUT',headers:HEADERS,body:JSON.stringify({enabled:enabled})})
        .then(function(r){ return r.json(); })
        .then(function(){ done++; if(done===names.length){ showToast(done+'개 '+(enabled?'활성':'비활성')+' 완료'); loadRooms(activeRoomPC); } })
        .catch(function(){ done++; });
    });
}
function bulkDelete() {
    var names = getCheckedRooms();
    if (!names.length) return;
    if (!confirm(names.length+'개 채팅방 삭제?')) return;
    var done = 0;
    names.forEach(function(name){
        fetch('/api/proxy/'+activeRoomPC+'/chatrooms/delete', {method:'POST',headers:HEADERS,body:JSON.stringify({name:name})})
        .then(function(r){ return r.json(); })
        .then(function(){ done++; if(done===names.length){ showToast(done+'개 삭제 완료'); loadRooms(activeRoomPC); } })
        .catch(function(){ done++; });
    });
}
function testSend(pcId, roomName) {
    showToast(roomName+' 테스트 발송 중...');
    fetch('/api/proxy/'+pcId+'/chatrooms/'+encodeURIComponent(roomName)+'/test', {method:'POST',headers:HEADERS})
    .then(function(r){ return r.json(); })
    .then(function(d){ showToast(d.ok?'테스트 태스크 등록: '+(d.task_id||''):'오류: '+(d.error||'')); })
    .catch(function(){ showToast('통신 오류'); });
}
function updateRoomControls(pcId) {
    document.getElementById('roomControls').innerHTML =
        '<button class="btn btn-sm btn-success" onclick="scanRoomsAdd(\''+pcId+'\')">스캔 후 추가</button> '+
        '<button class="btn btn-sm btn-secondary" onclick="scanRooms(\''+pcId+'\')">전체 재스캔</button>';
}
function scanRooms(pcId) {
    if (!confirm('기존 목록을 삭제하고 전체 재스캔합니다. 계속?')) return;
    showToast('전체 재스캔 등록 중...');
    fetch('/api/proxy/'+pcId+'/chatrooms/scan', {method:'POST',headers:HEADERS,body:JSON.stringify({mode:'scan'})})
    .then(function(r){ return r.json(); })
    .then(function(d){ showToast(d.ok?'재스캔 태스크: '+(d.task_id||''):'오류: '+(d.error||'')); })
    .catch(function(){ showToast('통신 오류'); });
}
function scanRoomsAdd(pcId) {
    showToast('스캔 후 추가 등록 중...');
    fetch('/api/proxy/'+pcId+'/chatrooms/scan', {method:'POST',headers:HEADERS,body:JSON.stringify({mode:'add'})})
    .then(function(r){ return r.json(); })
    .then(function(d){ showToast(d.ok?'스캔 추가 태스크: '+(d.task_id||''):'오류: '+(d.error||'')); })
    .catch(function(){ showToast('통신 오류'); });
}
function toggleCat(pcId, roomName, cat, el) {
    var on = el.style.borderColor==='rgb(34, 197, 94)';
    var newOn = !on;
    el.style.borderColor = newOn?'#22c55e':'#ddd';
    el.style.background = newOn?'#dcfce7':'#f9fafb';
    el.style.color = newOn?'#166534':'#999';
    // 같은 행의 모든 카테고리 태그에서 현재 상태 수집
    var siblings = el.parentElement.children;
    var cats = [];
    for (var i=0;i<siblings.length;i++) {
        if (siblings[i].style.borderColor==='rgb(34, 197, 94)') cats.push(siblings[i].textContent);
    }
    fetch('/api/proxy/'+pcId+'/chatrooms/'+encodeURIComponent(roomName), {method:'PUT',headers:HEADERS,body:JSON.stringify({categories:cats})})
    .then(function(r){ return r.json(); })
    .then(function(d){ if(!d.ok) showToast('오류: '+(d.error||'')); })
    .catch(function(){ showToast('통신 오류'); });
}
function updateRoomType(pcId, roomName, newType) {
    fetch('/api/proxy/'+pcId+'/chatrooms/'+encodeURIComponent(roomName), {method:'PUT',headers:HEADERS,body:JSON.stringify({type:newType})})
    .then(function(r){ return r.json(); })
    .then(function(d){ showToast(d.ok?roomName+' → '+(newType==='normal'?'일반':'오픈'):'오류: '+(d.error||'')); })
    .catch(function(){ showToast('통신 오류'); });
}
function toggleRoomEnabled(pcId, roomName, enabled) {
    fetch('/api/proxy/'+pcId+'/chatrooms/'+encodeURIComponent(roomName), {method:'PUT',headers:HEADERS,body:JSON.stringify({enabled:enabled})})
    .then(function(r){ return r.json(); })
    .then(function(d){ if(d.ok){ showToast(roomName+' → '+(enabled?'활성':'비활성')); loadRooms(pcId); } else showToast('오류'); })
    .catch(function(){ showToast('통신 오류'); });
}
function deleteRoom(pcId, roomName) {
    if (!confirm(roomName+' 삭제?')) return;
    fetch('/api/proxy/'+pcId+'/chatrooms/delete', {method:'POST',headers:HEADERS,body:JSON.stringify({name:roomName})})
    .then(function(r){ return r.json(); })
    .then(function(d){ showToast(d.ok?'삭제 완료':'오류: '+(d.error||'')); if(d.ok) loadRooms(pcId); })
    .catch(function(){ showToast('통신 오류'); });
}

/* ── Queue Control ── */
function queueControl(pcId, action) {
    fetch('/api/proxy/'+pcId+'/task/'+action, {method:'POST',headers:HEADERS})
    .then(function(r){ return r.json(); })
    .then(function(d){ showToast(pcId+' 큐: '+(d.ok?action:'오류')); loadHealth(); })
    .catch(function(){ showToast('통신 오류'); });
}
function clearQueue(pcId, count) {
    if (!confirm(pcId+' 대기 '+count+'건 전부 취소?')) return;
    fetch('/api/proxy/'+pcId+'/task/emergency-stop', {method:'POST',headers:HEADERS})
    .then(function(r){ return r.json(); })
    .then(function(d){ showToast(d.ok?d.message:'오류: '+(d.error||'')); loadHealth(); loadTasks(pcId); })
    .catch(function(){ showToast('통신 오류'); });
}

/* ── PC Toggle ── */
function togglePC(el, pcId) {
    fetch('/api/pcs/'+pcId+'/toggle', {method:'POST', headers:HEADERS})
    .then(function(r){ return r.json(); })
    .then(function(d){ showToast(pcId+' → '+(d.enabled?'활성':'비활성')); loadHealth(); })
    .catch(function(){ showToast('통신 오류'); loadHealth(); });
}

/* ── Health ── */
function loadHealth() {
    fetch('/api/health')
    .then(function(r){ return r.json(); })
    .then(function(d){ if (d.ok) renderPCs(d); })
    .catch(function(){ document.getElementById('refreshInfo').textContent = '연결 실패'; });
}

/* ── Task Submit ── */
function submitTask() {
    var btn = document.getElementById('submitBtn'), resEl = document.getElementById('submitResult');
    var type = document.getElementById('taskType').value;
    var fields = TYPE_FIELDS[type] || [];
    var taskData = {};
    fields.forEach(function(f) {
        var el = document.getElementById('field_' + f.key);
        if (!el) return;
        var val = el.value.trim();
        if (f.key === 'links') {
            taskData[f.key] = val.split('\n').filter(function(l){ return l.trim(); });
        } else {
            taskData[f.key] = val;
        }
    });
    if (type === 'scan_open_chatrooms') taskData = {mode:'scan'};
    btn.disabled = true; btn.textContent = '전송 중...';
    fetch('/api/task/submit', {method:'POST',headers:HEADERS,body:JSON.stringify({type:type,data:taskData,priority:2})})
    .then(function(r){ return r.json(); })
    .then(function(d){
        btn.disabled = false; btn.textContent = '전송';
        if (d.ok||d.task_id) showResult(resEl, (TYPE_LABELS[type]||type)+' 전송 완료', 'success');
        else showResult(resEl, '오류: '+(d.error||''), 'error');
    })
    .catch(function(e){ btn.disabled=false; btn.textContent='전송'; showResult(resEl,'통신 오류: '+e.message,'error'); });
}

/* ── Task Lookup ── */
function checkTask() {
    var resEl = document.getElementById('statusResult');
    var taskId = document.getElementById('taskIdInput').value.trim();
    if (!taskId) { showResult(resEl, '태스크 ID를 입력하세요', 'error'); return; }
    fetch('/api/task/status/'+taskId)
    .then(function(r){ return r.json(); })
    .then(function(d){
        if (d.ok) {
            var t = d.task||{}, info = extractInfo(t), lines = [];
            lines.push('담당 PC: '+(d.pc||'-'));
            lines.push('상태: '+statusLabel(t.status));
            lines.push('유형: '+(TYPE_LABELS[t.type]||t.type||'-'));
            lines.push('생성: '+formatTime(t.created_at));
            if (info.target) lines.push('대상: '+info.target);
            if (info.content) lines.push('내용: '+info.content);
            lines.push('결과: '+extractResult(t));
            showResult(resEl, lines.join('\n'), t.status==='done'?'success':(t.status==='failed'?'error':'info'));
        } else showResult(resEl, '태스크 없음', 'error');
    })
    .catch(function(e){ showResult(resEl, '통신 오류: '+e.message, 'error'); });
}

/* ── Cooldown ── */
var _cooldownTimer = null;
function loadCooldown() {
    fetch('/api/promotion/cooldown', {headers: HEADERS})
    .then(function(r){ return r.json(); })
    .then(function(d){
        if (!d.ok) return;
        var el = document.getElementById('cooldownList');
        var info = document.getElementById('cooldownInfo');
        var rooms = d.rooms || {};
        var names = Object.keys(rooms).sort();
        info.textContent = '쿨다운: ' + d.cooldown_minutes + '분 / 채팅방: ' + names.length + '개';
        if (!names.length) { el.innerHTML = '<div style="color:#999;font-size:13px">기록 없음</div>'; return; }
        var html = '';
        names.forEach(function(name){
            var room = rooms[name];
            var rem = room.min_remaining_sec;
            var ready = room.all_ready;
            var min = Math.floor(rem/60), sec = rem%60;
            var timeStr = ready ? '준비완료' : min + '분 ' + (sec<10?'0':'') + sec + '초';
            var color = ready ? '#22c55e' : '#f59e0b';
            var bg = ready ? '#f0fdf4' : '#fffbeb';
            var pct = ready ? 100 : Math.min(100, ((d.cooldown_minutes*60 - rem) / (d.cooldown_minutes*60)) * 100);
            html += '<div style="padding:8px 12px;border:1px solid #e5e7eb;border-radius:8px;background:'+bg+'">'
                + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
                + '<span style="font-weight:600;font-size:13px">'+name+'</span>'
                + '<span style="font-weight:700;font-size:13px;color:'+color+'">'+timeStr+'</span>'
                + '</div>'
                + '<div style="height:4px;background:#e5e7eb;border-radius:2px;overflow:hidden">'
                + '<div style="height:100%;width:'+pct+'%;background:'+color+';border-radius:2px;transition:width 1s"></div>'
                + '</div></div>';
        });
        el.innerHTML = html;
    }).catch(function(){});
}
function startCooldownTimer() {
    loadCooldown();
    if (_cooldownTimer) clearInterval(_cooldownTimer);
    _cooldownTimer = setInterval(loadCooldown, 5000);
}

/* ── Promotion Control ── */
function togglePromotion(start) {
    var url = start ? '/api/promotion/start' : '/api/promotion/stop';
    var btn = start ? document.getElementById('promoStartBtn') : document.getElementById('promoStopBtn');
    btn.disabled = true; btn.textContent = '처리중...';
    fetch(url, {method:'POST',headers:HEADERS,body:JSON.stringify({password:'4444'})})
    .then(function(r){ return r.json(); })
    .then(function(d){
        btn.textContent = start ? '홍보 시작' : '홍보 중지';
        btn.disabled = false;
        checkPromoStatus();
        if (d.ok) alert(start ? '홍보 시작됨' : '홍보 중지됨');
        else alert('실패: ' + (d.error||d.message||''));
    }).catch(function(e){ btn.textContent = start?'홍보 시작':'홍보 중지'; btn.disabled=false; alert('오류: '+e); });
}
function checkPromoStatus() {
    var el = document.getElementById('promoStatus');
    // PC2 (홍보 담당) config에서 상태 확인
    fetch('/api/config', {headers:HEADERS}).then(function(r){return r.json();}).then(function(d){
        if (!d.ok) return;
        var pcs = d.pcs || {};
        // promotion 역할 PC 찾기
        var promoPC = null;
        for (var id in pcs) { if ((pcs[id].roles||[]).indexOf('promotion')!==-1) { promoPC = id; break; } }
        if (!promoPC) { el.textContent='PC없음'; el.style.background='#f3f4f6'; el.style.color='#666'; return; }
        // 해당 PC의 config에서 promotion_active 확인
        fetch('/api/proxy/'+promoPC+'/api/config', {headers:HEADERS}).then(function(r){return r.json();}).then(function(cfg){
            var active = cfg.promotion_active || cfg.is_promoting;
            if (active) { el.textContent='홍보중'; el.style.background='#dcfce7'; el.style.color='#166534'; }
            else { el.textContent='중지'; el.style.background='#fee2e2'; el.style.color='#991b1b'; }
        }).catch(function(){ el.textContent='확인불가'; el.style.background='#fef3c7'; el.style.color='#92400e'; });
    }).catch(function(){});
}

/* ── Init ── */
updateFields();
loadHealth();
setInterval(loadHealth, 10000);
startCooldownTimer();
checkPromoStatus();
setInterval(checkPromoStatus, 15000);
</script>
</body>
</html>"""


@app.route("/")
def dashboard():
    """미들웨어 대시보드"""
    return render_template_string(DASHBOARD_HTML, api_key=_get_config().get("api_key", ""))


# ─── PC API 프록시 ───

@app.route("/api/proxy/<pc_id>/<path:api_path>", methods=["GET", "POST", "PUT", "DELETE"])
def api_proxy(pc_id, api_path):
    """PC API 프록시 — /api/proxy/pc1/task/recent → PC1의 /api/task/recent"""
    cfg = _get_config()
    pcs = cfg.get("pcs", {})
    if pc_id not in pcs:
        return jsonify({"ok": False, "error": f"PC 없음: {pc_id}"}), 404

    pc = pcs[pc_id]
    pc_url = pc["url"].rstrip("/")
    headers = {"X-API-Key": cfg.get("api_key", "")}

    try:
        url = f"{pc_url}/api/{api_path}"
        body = request.get_json(silent=True)
        if request.method == "POST":
            resp = requests.post(url, json=body, headers=headers, timeout=10)
        elif request.method == "PUT":
            resp = requests.put(url, json=body, headers=headers, timeout=10)
        elif request.method == "DELETE":
            resp = requests.delete(url, json=body, headers=headers, timeout=10)
        else:
            resp = requests.get(url, headers=headers, timeout=10)
        return resp.content, resp.status_code, {"Content-Type": "application/json; charset=utf-8"}
    except requests.Timeout:
        return jsonify({"ok": False, "error": "타임아웃"}), 504
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


# ─── 태스크 라우팅 API ───

@app.route("/api/task/submit", methods=["POST"])
def api_task_submit():
    """태스크를 담당 PC로 라우팅"""
    if not _check_api_key():
        return jsonify({"ok": False, "error": "인증 실패"}), 401

    data = request.get_json(silent=True) or {}
    task_type = data.get("type", "")
    if not task_type:
        return jsonify({"ok": False, "error": "type 필수"}), 400

    # 홍보는 방별 쿨다운 기반 스마트 라우팅
    task_data = data.get("data", {})
    if task_type == "promotion" and task_data.get("room_name"):
        pc_id, pc = _find_pc_for_promotion(task_data["room_name"])
    else:
        pc_id, pc = _find_pc_for_type(task_type)

    if not pc:
        logger.warning("담당 PC 없음: type=%s", task_type)
        return jsonify({"ok": False, "error": f"담당 PC 없음: {task_type}"}), 503

    pc_url = pc["url"].rstrip("/")
    logger.info("태스크 라우팅: type=%s → %s (%s)", task_type, pc_id, pc_url)

    try:
        cfg = _get_config()
        resp = requests.post(
            f"{pc_url}/api/task/submit",
            json=data,
            headers={
                "X-API-Key": cfg.get("api_key", ""),
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        result = resp.json()
        logger.info("PC 응답: %s → %s", pc_id, result)

        # 홍보 전송 성공 시 발송 기록 저장
        if task_type == "promotion" and resp.ok and task_data.get("room_name"):
            _record_promotion(task_data["room_name"], pc_id)

        return jsonify(result), resp.status_code
    except requests.Timeout:
        logger.error("PC 타임아웃: %s (%s)", pc_id, pc_url)
        return jsonify({"ok": False, "error": f"PC 타임아웃: {pc_id}"}), 504
    except Exception as e:
        logger.error("PC 연결 실패: %s → %s", pc_id, e)
        return jsonify({"ok": False, "error": f"PC 연결 실패: {pc_id}"}), 503


@app.route("/api/task/cancel-campaign", methods=["POST"])
def api_task_cancel_campaign():
    """홍보 role PC에 캠페인 취소 전달"""
    if not _check_api_key():
        return jsonify({"ok": False, "error": "인증 실패"}), 401

    data = request.get_json(silent=True) or {}
    campaign_id = data.get("campaign_id", "")
    if not campaign_id:
        return jsonify({"ok": False, "error": "campaign_id 필수"}), 400

    # promotion role을 가진 모든 PC에 전달
    pcs = _find_pcs_with_role("promotion")
    if not pcs:
        return jsonify({"ok": True, "cancelled": 0, "message": "홍보 PC 없음"})

    total_cancelled = 0
    cfg = _get_config()
    for pc_id, pc in pcs:
        pc_url = pc["url"].rstrip("/")
        try:
            resp = requests.post(
                f"{pc_url}/api/task/cancel-campaign",
                json={"campaign_id": campaign_id},
                headers={"X-API-Key": cfg.get("api_key", "")},
                timeout=5,
            )
            if resp.ok:
                count = resp.json().get("cancelled", 0)
                total_cancelled += count
                logger.info("캠페인 취소: %s → %d건", pc_id, count)
        except Exception as e:
            logger.warning("캠페인 취소 전달 실패: %s → %s", pc_id, e)

    return jsonify({"ok": True, "cancelled": total_cancelled})


# ─── 홍보 제어 프록시 (Railway → 미들웨어 → PC1) ───

@app.route("/api/config")
def api_config_proxy():
    """PC1 config 조회 프록시"""
    pc_id, pc = _find_pc_for_type("promotion")
    if not pc:
        # promotion 역할 없으면 PC1 기본
        pc_id, pc = _find_pc_for_type("friend_add")
    if not pc:
        return jsonify({"ok": False, "error": "PC 없음"}), 503

    pc_url = pc["url"].rstrip("/")
    cfg = _get_config()
    try:
        resp = requests.get(
            f"{pc_url}/api/config",
            headers={"X-API-Key": cfg.get("api_key", "")},
            timeout=5,
        )
        return resp.content, resp.status_code, {"Content-Type": "application/json; charset=utf-8"}
    except Exception as e:
        logger.error("config 프록시 실패: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 503


@app.route("/api/promotion/start", methods=["POST"])
def api_promotion_start_proxy():
    """홍보 시작 프록시"""
    pc_id, pc = _find_pc_for_type("promotion")
    if not pc:
        pc_id, pc = _find_pc_for_type("friend_add")
    if not pc:
        return jsonify({"ok": False, "error": "PC 없음"}), 503

    pc_url = pc["url"].rstrip("/")
    logger.info("홍보 시작 → %s (%s)", pc_id, pc_url)
    try:
        resp = requests.post(
            f"{pc_url}/api/promotion/start",
            json=request.get_json(silent=True) or {"password": "4444"},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        return resp.content, resp.status_code, {"Content-Type": "application/json; charset=utf-8"}
    except Exception as e:
        logger.error("홍보 시작 프록시 실패: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 503


@app.route("/api/promotion/stop", methods=["POST"])
def api_promotion_stop_proxy():
    """홍보 중지 프록시"""
    pc_id, pc = _find_pc_for_type("promotion")
    if not pc:
        pc_id, pc = _find_pc_for_type("friend_add")
    if not pc:
        return jsonify({"ok": False, "error": "PC 없음"}), 503

    pc_url = pc["url"].rstrip("/")
    logger.info("홍보 중지 → %s (%s)", pc_id, pc_url)
    try:
        resp = requests.post(
            f"{pc_url}/api/promotion/stop",
            timeout=10,
        )
        return resp.content, resp.status_code, {"Content-Type": "application/json; charset=utf-8"}
    except Exception as e:
        logger.error("홍보 중지 프록시 실패: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 503


# ─── 콜백 프록시 ───

@app.route("/api/callback/friend-add", methods=["POST"])
def api_callback_friend_add():
    """PC → 미들웨어 → Railway 친구추가 콜백 프록시"""
    data = request.get_json(silent=True) or {}
    cfg = _get_config()
    railway_url = cfg.get("railway_url", "").rstrip("/")

    if not railway_url:
        logger.error("railway_url 미설정")
        return jsonify({"ok": False, "error": "railway_url 미설정"}), 500

    name = data.get("name", "")
    phone = data.get("phone", "")
    success = data.get("success", False)
    logger.info("콜백 프록시: friend-add %s %s → %s", name, phone, success)

    try:
        resp = requests.post(
            f"{railway_url}/api/callback/friend-add",
            json=data,
            headers={"X-API-Key": cfg.get("api_key", "")},
            timeout=10,
        )
        logger.info("Railway 콜백 전달 완료: status=%d", resp.status_code)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        logger.error("Railway 콜백 전달 실패: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 502


# ─── 헬스체크 ───

_health_status = {}  # {pc_id: {"healthy": bool, "fail_count": int, "last_check": str, "queue_status": dict}}


def _health_check_loop():
    """30초 주기 PC 헬스체크"""
    while True:
        time.sleep(30)
        _cleanup_promotion_log()
        cfg = _get_config()
        pcs = cfg.get("pcs", {})

        for pc_id, pc in pcs.items():
            if not pc.get("enabled", True):
                continue

            pc_url = pc["url"].rstrip("/")
            now = datetime.now(KST).isoformat()

            try:
                resp = requests.get(f"{pc_url}/api/task/queue-status", timeout=5)
                if resp.ok:
                    queue_status = resp.json()
                    _health_status[pc_id] = {
                        "healthy": True,
                        "fail_count": 0,
                        "last_check": now,
                        "queue_status": queue_status,
                    }
                    # config에 healthy 반영
                    with _config_lock:
                        if pc_id in _config.get("pcs", {}):
                            _config["pcs"][pc_id]["healthy"] = True
                    continue
            except Exception:
                pass

            # 실패
            prev = _health_status.get(pc_id, {"fail_count": 0})
            fail_count = prev.get("fail_count", 0) + 1
            healthy = fail_count < 3

            _health_status[pc_id] = {
                "healthy": healthy,
                "fail_count": fail_count,
                "last_check": now,
                "queue_status": prev.get("queue_status", {}),
            }

            with _config_lock:
                if pc_id in _config.get("pcs", {}):
                    _config["pcs"][pc_id]["healthy"] = healthy

            if not healthy:
                logger.warning("PC 헬스체크 실패 (%d회 연속): %s (%s)", fail_count, pc_id, pc_url)


# ─── 관리 API ───

@app.route("/api/health")
def api_health():
    """전체 PC 헬스 상태"""
    cfg = _get_config()
    pcs = cfg.get("pcs", {})
    result = {}
    for pc_id, pc in pcs.items():
        health = _health_status.get(pc_id, {})
        result[pc_id] = {
            "name": pc.get("name", pc_id),
            "url": pc.get("url", ""),
            "enabled": pc.get("enabled", True),
            "healthy": health.get("healthy", True),
            "fail_count": health.get("fail_count", 0),
            "last_check": health.get("last_check", ""),
            "queue_status": health.get("queue_status", {}),
            "roles": pc.get("roles", []),
        }
    return jsonify({"ok": True, "pcs": result})


@app.route("/api/pcs")
def api_pcs():
    """PC 목록 조회"""
    cfg = _get_config()
    pcs = cfg.get("pcs", {})
    result = {}
    for pc_id, pc in pcs.items():
        result[pc_id] = {
            "name": pc.get("name", pc_id),
            "url": pc.get("url", ""),
            "roles": pc.get("roles", []),
            "enabled": pc.get("enabled", True),
            "healthy": pc.get("healthy", True),
        }
    return jsonify({"ok": True, "pcs": result})


@app.route("/api/pcs/<pc_id>/toggle", methods=["POST"])
def api_pc_toggle(pc_id):
    """PC 활성/비활성 토글"""
    if not _check_api_key():
        return jsonify({"ok": False, "error": "인증 실패"}), 401

    with _config_lock:
        pcs = _config.get("pcs", {})
        if pc_id not in pcs:
            return jsonify({"ok": False, "error": f"PC 없음: {pc_id}"}), 404
        pcs[pc_id]["enabled"] = not pcs[pc_id].get("enabled", True)
        enabled = pcs[pc_id]["enabled"]
    _save_config()
    logger.info("PC 토글: %s → enabled=%s", pc_id, enabled)
    return jsonify({"ok": True, "pc_id": pc_id, "enabled": enabled})


# ─── 태스크 상태 프록시 (선택적) ───

@app.route("/api/task/status/<task_id>")
def api_task_status(task_id):
    """태스크 상태 조회 — 모든 PC에 순차 질의"""
    cfg = _get_config()
    pcs = cfg.get("pcs", {})
    for pc_id, pc in pcs.items():
        if not pc.get("enabled", True):
            continue
        pc_url = pc["url"].rstrip("/")
        try:
            resp = requests.get(f"{pc_url}/api/task/status/{task_id}", timeout=5)
            if resp.ok:
                data = resp.json()
                if data.get("ok"):
                    data["pc"] = pc_id
                    return jsonify(data)
        except Exception:
            continue
    return jsonify({"ok": False, "error": "태스크 없음"}), 404


@app.route("/api/task/queue-status")
def api_queue_status():
    """모든 PC의 큐 상태 통합"""
    cfg = _get_config()
    pcs = cfg.get("pcs", {})
    total = {"pending_count": 0, "processing_count": 0}
    per_pc = {}
    for pc_id, pc in pcs.items():
        if not pc.get("enabled", True):
            continue
        health = _health_status.get(pc_id, {})
        qs = health.get("queue_status", {})
        per_pc[pc_id] = qs
        total["pending_count"] += qs.get("pending_count", 0)
        total["processing_count"] += qs.get("processing_count", 0)
    return jsonify({"ok": True, "total": total, "per_pc": per_pc})


@app.route("/api/promotion/cooldown")
def api_promotion_cooldown():
    """채팅방별 쿨다운 남은 시간 조회"""
    if not _check_api_key():
        return jsonify({"ok": False, "error": "인증 실패"}), 401

    cfg = _get_config()
    cooldown_min = cfg.get("promotion_cooldown_minutes", 30)
    cooldown_sec = cooldown_min * 60
    now = datetime.now(KST)

    rooms = {}
    for room_name, pcs in _promotion_log.items():
        pc_status = {}
        for pc_id, last_send in pcs.items():
            elapsed = (now - last_send).total_seconds()
            remaining = max(0, cooldown_sec - elapsed)
            pc_status[pc_id] = {
                "last_send": last_send.strftime("%H:%M:%S"),
                "elapsed_sec": int(elapsed),
                "remaining_sec": int(remaining),
                "ready": remaining <= 0,
            }
        # 방 전체 ready = 모든 PC ready
        all_ready = all(s["ready"] for s in pc_status.values())
        min_remaining = min((s["remaining_sec"] for s in pc_status.values()), default=0)
        rooms[room_name] = {
            "pcs": pc_status,
            "all_ready": all_ready,
            "min_remaining_sec": min_remaining,
        }

    return jsonify({"ok": True, "cooldown_minutes": cooldown_min, "rooms": rooms})


# ─── 메인 ───

if __name__ == "__main__":
    _load_config()
    logger.info("미들웨어 서버 시작 (포트 6100)")
    logger.info("등록된 PC: %s", list(_config.get("pcs", {}).keys()))

    # 헬스체크 스레드
    health_thread = threading.Thread(target=_health_check_loop, daemon=True, name="health-checker")
    health_thread.start()

    app.run(host="0.0.0.0", port=6100, debug=False)

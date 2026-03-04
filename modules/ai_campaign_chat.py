"""AI 대화형 캠페인 관리 엔진 (Claude Sonnet)"""
import json
import logging
import os
from anthropic import Anthropic

import models

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """당신은 카비서 캠페인 관리 도우미입니다.
캠페인 등록, 수정, 현황 조회를 대화형으로 처리합니다.

## 캠페인 필드 (한국어명 → DB컬럼)
- 캠페인유형(campaign_type): "실배송" | "빈박스"  [기본: 실배송]
- 플랫폼(platform): "쿠팡" | "네이버" | "11번가" | "G마켓" | "옥션" | "올리브영" | "오늘의집" | 기타
- 업체명(company): 텍스트
- 캠페인명(campaign_name): 텍스트 [없으면 상품명으로]
- 상품명(product_name): 텍스트 [필수]
- 옵션(options): 텍스트
- 상품링크(product_link): URL
- 상품이미지(product_image): URL
- 결제금액(payment_amount): 정수(원) [필수]
- 리뷰비(review_fee): 정수(원) [관리자 전용]
- 총수량(total_qty): 정수 [필수]
- 일수량(daily_qty): 정수 [기본: 총수량]
- 진행일수(duration_days): 정수 [기본: 1]
- 1인일일제한(max_per_person_daily): 정수 [기본: 0=무제한]
- 구매가능시간(buy_time): "HH:MM~HH:MM" [기본: 제한없음]
- 중복허용(allow_duplicate): "Y"|"N"|"텀:30" [기본: N]
- 키워드(keyword): 텍스트
- 유입방식(entry_method): "키워드검색"|"링크유입"|"기타"
- 리뷰기한일수(review_deadline_days): 정수
- 공개여부(is_public): "Y"|"N" [기본: Y]
- 캠페인가이드(campaign_guide): 텍스트
- 추가안내사항(extra_info): 텍스트

## 캠페인 상태값
모집중, 진행중, 마감, 종료, 승인대기, 대행사승인, 반려, 임시저장

## 상태 워크플로우
- 관리자 생성 → "모집중"
- 대행사 생성 → "대행사승인"
- 클라이언트 생성 → "승인대기"

## 규칙
1. 등록 시 필수(상품명, 플랫폼, 총수량, 결제금액) 빠지면 되물을 것
2. 수정 시 캠페인 ID 또는 이름으로 특정 → 변경 필드만 업데이트
3. 현황 요청 시 적절한 도구 호출 후 보기 좋게 정리
4. 모든 정보 모이면 요약 → 확인 후 실행
5. 한국어 대화, 간결하게
6. 이미지 URL이 전달되면 상품이미지로 활용
"""

# Tool definitions for Claude
TOOLS = [
    {
        "name": "create_campaign",
        "description": "새 캠페인을 생성합니다. 필수: 상품명, 플랫폼, 총수량, 결제금액",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_name": {"type": "string", "description": "상품명 (필수)"},
                "platform": {"type": "string", "description": "쿠팡/네이버/11번가 등 (필수)"},
                "total_qty": {"type": "integer", "description": "총 모집 수량 (필수)"},
                "payment_amount": {"type": "integer", "description": "결제금액(원) (필수)"},
                "campaign_name": {"type": "string", "description": "캠페인명"},
                "company": {"type": "string", "description": "업체명"},
                "campaign_type": {"type": "string", "enum": ["실배송", "빈박스"], "description": "캠페인유형"},
                "options": {"type": "string", "description": "옵션"},
                "product_link": {"type": "string", "description": "상품링크"},
                "product_image": {"type": "string", "description": "상품이미지 URL"},
                "review_fee": {"type": "integer", "description": "리뷰비"},
                "daily_qty": {"type": "integer", "description": "일수량"},
                "duration_days": {"type": "integer", "description": "진행일수"},
                "max_per_person_daily": {"type": "integer", "description": "1인일일제한"},
                "buy_time": {"type": "string", "description": "구매가능시간 (HH:MM~HH:MM)"},
                "allow_duplicate": {"type": "string", "description": "중복허용 (Y/N/텀:30)"},
                "keyword": {"type": "string", "description": "키워드"},
                "entry_method": {"type": "string", "description": "유입방식"},
                "review_deadline_days": {"type": "integer", "description": "리뷰기한일수"},
                "is_public": {"type": "string", "description": "공개여부 (Y/N)"},
                "campaign_guide": {"type": "string", "description": "캠페인가이드"},
                "extra_info": {"type": "string", "description": "추가안내사항"}
            },
            "required": ["product_name", "platform", "total_qty", "payment_amount"]
        }
    },
    {
        "name": "update_campaign",
        "description": "기존 캠페인을 수정합니다. 캠페인 ID와 변경할 필드를 지정합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "캠페인 ID"},
                "changes": {
                    "type": "object",
                    "description": "변경할 필드와 값. 키는 한국어 시트 컬럼명 (상품명, 플랫폼, 총수량, 결제금액, 일수량, 진행일수 등)"
                }
            },
            "required": ["campaign_id", "changes"]
        }
    },
    {
        "name": "get_campaign",
        "description": "캠페인 상세 정보를 조회합니다. ID 또는 이름으로 검색합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "캠페인 ID"},
                "search_name": {"type": "string", "description": "캠페인명으로 검색 (ID 없을 때)"}
            }
        }
    },
    {
        "name": "list_campaigns",
        "description": "캠페인 목록을 조회합니다. 상태 필터링, 페이지네이션 지원.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "상태 필터 (모집중/진행중/마감/종료 등)"},
                "page": {"type": "integer", "description": "페이지 번호 (기본 1)"},
                "per_page": {"type": "integer", "description": "페이지당 건수 (기본 20)"}
            }
        }
    },
    {
        "name": "get_dashboard_stats",
        "description": "대시보드 통계를 조회합니다. 진행중/마감/신청대기 건수 등.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "list_clients",
        "description": "등록된 업체(클라이언트) 목록을 조회합니다.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "list_agencies",
        "description": "등록된 대행사 목록을 조회합니다.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    }
]


class AICampaignChat:
    """AI 대화형 캠페인 관리 엔진"""

    def __init__(self):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다")
        self.client = Anthropic(api_key=api_key)
        self.model = "claude-sonnet-4-20250514"

    def _get_tools_for_portal(self, portal: str) -> list:
        """포탈별 사용 가능한 도구 필터링"""
        if portal == "admin":
            return TOOLS  # 전체 접근
        elif portal == "agency":
            # 대행사: 클라이언트 목록, 캠페인 CRUD, 대시보드
            return [t for t in TOOLS if t["name"] != "list_agencies"]
        else:  # client
            # 클라이언트: 본인 캠페인만, 대행사/클라이언트 목록 제외
            return [t for t in TOOLS if t["name"] not in ("list_agencies", "list_clients")]

    def _get_system_prompt(self, portal: str) -> str:
        """포탈별 시스템 프롬프트 조정"""
        base = SYSTEM_PROMPT
        if portal == "agency":
            base += "\n\n## 포탈: 대행사\n- 소속 클라이언트의 캠페인만 접근 가능\n- 캠페인 생성 시 상태는 '대행사승인'으로 설정\n- 리뷰비 설정 불가"
        elif portal == "client":
            base += "\n\n## 포탈: 클라이언트\n- 본인 캠페인만 접근 가능\n- 캠페인 생성 시 상태는 '승인대기'로 설정\n- 리뷰비, 홍보 관련 필드 설정 불가"
        return base

    def chat(self, messages: list, portal: str = "admin", owner_id=None) -> dict:
        """
        대화 처리.
        messages: [{"role": "user"|"assistant", "content": "..."}]
        portal: "admin"|"agency"|"client"
        owner_id: agency_id or client_id (for filtering)
        Returns: {"reply": "...", "messages": updated_messages}
        """
        tools = self._get_tools_for_portal(portal)
        system = self._get_system_prompt(portal)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system,
                tools=tools,
                messages=messages
            )
        except Exception as e:
            logger.error(f"Claude API 에러: {e}")
            return {"reply": f"AI 서비스 오류가 발생했습니다: {str(e)}", "messages": messages}

        # Process response - handle tool use loop
        return self._process_response(response, messages, tools, system, portal, owner_id)

    def _process_response(self, response, messages, tools, system, portal, owner_id, depth=0):
        """응답 처리 (tool_use 루프 포함)"""
        if depth > 5:
            return {"reply": "처리 중 오류가 발생했습니다. 다시 시도해주세요.", "messages": messages}

        # Collect assistant content blocks — serialize to plain dicts
        assistant_content = response.content
        serialized = []
        for block in assistant_content:
            if block.type == "text":
                serialized.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                serialized.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
        messages.append({"role": "assistant", "content": serialized})

        # Check if there are tool uses
        tool_uses = [b for b in serialized if b["type"] == "tool_use"]

        if not tool_uses:
            # No tool use — extract text reply
            text_parts = [b["text"] for b in serialized if b.get("type") == "text"]
            reply = "\n".join(text_parts) if text_parts else ""
            return {"reply": reply, "messages": messages}

        # Execute each tool and collect results
        tool_results = []
        for tool_use in tool_uses:
            result = self._execute_tool(tool_use["name"], tool_use["input"], portal, owner_id)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use["id"],
                "content": json.dumps(result, ensure_ascii=False)
            })

        messages.append({"role": "user", "content": tool_results})

        # Call Claude again with tool results
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system,
                tools=tools,
                messages=messages
            )
        except Exception as e:
            logger.error(f"Claude API 에러 (tool result): {e}")
            return {"reply": f"AI 서비스 오류: {str(e)}", "messages": messages}

        return self._process_response(response, messages, tools, system, portal, owner_id, depth + 1)

    def _execute_tool(self, name: str, input_data: dict, portal: str, owner_id) -> dict:
        """도구 실행"""
        try:
            if name == "create_campaign":
                return self._do_create(input_data, portal, owner_id)
            elif name == "update_campaign":
                return self._do_update(input_data, portal, owner_id)
            elif name == "get_campaign":
                return self._do_get(input_data, portal, owner_id)
            elif name == "list_campaigns":
                return self._do_list(input_data, portal, owner_id)
            elif name == "get_dashboard_stats":
                return self._do_stats(portal, owner_id)
            elif name == "list_clients":
                return self._do_list_clients(portal, owner_id)
            elif name == "list_agencies":
                return self._do_list_agencies()
            else:
                return {"ok": False, "error": f"알 수 없는 도구: {name}"}
        except Exception as e:
            logger.error(f"도구 실행 에러 [{name}]: {e}", exc_info=True)
            return {"ok": False, "error": str(e)}

    def _do_create(self, data: dict, portal: str, owner_id) -> dict:
        """캠페인 생성"""
        db = models.db_manager
        if not db:
            return {"ok": False, "error": "DB 연결 없음"}

        # Build campaign data with sheet column names
        campaign_data = {}

        # Map English tool keys to Korean sheet column names
        field_map = {
            "product_name": "상품명", "platform": "플랫폼", "total_qty": "총수량",
            "payment_amount": "결제금액", "campaign_name": "캠페인명", "company": "업체명",
            "campaign_type": "캠페인유형", "options": "옵션", "product_link": "상품링크",
            "product_image": "상품이미지", "review_fee": "리뷰비", "daily_qty": "일수량",
            "duration_days": "진행일수", "max_per_person_daily": "1인일일제한",
            "buy_time": "구매가능시간", "allow_duplicate": "중복허용",
            "keyword": "키워드", "entry_method": "유입방식",
            "review_deadline_days": "리뷰기한일수", "is_public": "공개여부",
            "campaign_guide": "캠페인가이드", "extra_info": "추가안내사항"
        }

        for k, v in data.items():
            if k in field_map and v is not None:
                campaign_data[field_map[k]] = v

        # Set defaults
        if "캠페인명" not in campaign_data:
            campaign_data["캠페인명"] = campaign_data.get("상품명", "")
        if "캠페인유형" not in campaign_data:
            campaign_data["캠페인유형"] = "실배송"
        if "공개여부" not in campaign_data:
            campaign_data["공개여부"] = "Y"
        if "중복허용" not in campaign_data:
            campaign_data["중복허용"] = "N"

        # Portal-specific status
        if portal == "admin":
            campaign_data["상태"] = "모집중"
        elif portal == "agency":
            campaign_data["상태"] = "대행사승인"
            # Remove admin-only fields
            campaign_data.pop("리뷰비", None)
        elif portal == "client":
            campaign_data["상태"] = "승인대기"
            campaign_data.pop("리뷰비", None)

        # Set owner
        if portal == "agency" and owner_id:
            campaign_data["대행사ID"] = owner_id
        elif portal == "client" and owner_id:
            campaign_data["클라이언트ID"] = owner_id

        campaign_id = db.create_campaign(campaign_data)
        return {"ok": True, "campaign_id": campaign_id, "message": f"캠페인 등록 완료 (ID: {campaign_id})"}

    def _do_update(self, data: dict, portal: str, owner_id) -> dict:
        """캠페인 수정"""
        db = models.db_manager
        if not db:
            return {"ok": False, "error": "DB 연결 없음"}

        campaign_id = data.get("campaign_id", "")
        changes = data.get("changes", {})
        if not campaign_id or not changes:
            return {"ok": False, "error": "캠페인 ID와 변경 내용이 필요합니다"}

        # Check campaign exists and access permission
        campaign = db.get_campaign_by_id(campaign_id)
        if not campaign:
            return {"ok": False, "error": f"캠페인을 찾을 수 없습니다: {campaign_id}"}

        # Portal-based access check
        if portal == "agency" and owner_id:
            if str(campaign.get("대행사ID", "")) != str(owner_id):
                return {"ok": False, "error": "접근 권한이 없습니다"}
            changes.pop("리뷰비", None)
        elif portal == "client" and owner_id:
            if str(campaign.get("클라이언트ID", "")) != str(owner_id):
                return {"ok": False, "error": "접근 권한이 없습니다"}
            changes.pop("리뷰비", None)

        db.update_campaign(campaign_id, changes)
        return {"ok": True, "campaign_id": campaign_id, "message": f"캠페인 수정 완료", "changes": changes}

    def _do_get(self, data: dict, portal: str, owner_id) -> dict:
        """캠페인 조회"""
        db = models.db_manager
        if not db:
            return {"ok": False, "error": "DB 연결 없음"}

        campaign_id = data.get("campaign_id", "")
        search_name = data.get("search_name", "")

        if campaign_id:
            campaign = db.get_campaign_by_id(campaign_id)
            if not campaign:
                return {"ok": False, "error": f"캠페인을 찾을 수 없습니다: {campaign_id}"}
            # Access check
            if not self._check_access(campaign, portal, owner_id):
                return {"ok": False, "error": "접근 권한이 없습니다"}
            return {"ok": True, "campaign": campaign}
        elif search_name:
            # Search by name
            all_campaigns = self._get_filtered_campaigns(portal, owner_id)
            matches = [c for c in all_campaigns if search_name.lower() in c.get("캠페인명", "").lower()
                       or search_name.lower() in c.get("상품명", "").lower()]
            if not matches:
                return {"ok": False, "error": f"'{search_name}' 관련 캠페인을 찾을 수 없습니다"}
            return {"ok": True, "campaigns": matches[:10], "total": len(matches)}
        else:
            return {"ok": False, "error": "캠페인 ID 또는 검색어가 필요합니다"}

    def _do_list(self, data: dict, portal: str, owner_id) -> dict:
        """캠페인 목록"""
        db = models.db_manager
        if not db:
            return {"ok": False, "error": "DB 연결 없음"}

        status = data.get("status", "")
        page = data.get("page", 1)
        per_page = data.get("per_page", 20)

        if portal == "admin":
            campaigns, total = db.get_campaigns_page(page, per_page, status)
        else:
            all_campaigns = self._get_filtered_campaigns(portal, owner_id)
            if status:
                all_campaigns = [c for c in all_campaigns if c.get("상태") == status]
            total = len(all_campaigns)
            start = (page - 1) * per_page
            campaigns = all_campaigns[start:start + per_page]

        # Add stats
        stats = db.get_campaign_stats() or {}
        for c in campaigns:
            cid = c.get("캠페인ID", "")
            if cid in stats:
                c["_stats"] = stats[cid]

        return {"ok": True, "campaigns": campaigns, "total": total, "page": page}

    def _do_stats(self, portal: str, owner_id) -> dict:
        """대시보드 통계"""
        db = models.db_manager
        if not db:
            return {"ok": False, "error": "DB 연결 없음"}

        campaigns = self._get_filtered_campaigns(portal, owner_id)
        stats = db.get_campaign_stats() or {}

        status_counts = {}
        total_active = 0
        total_done = 0
        total_today = 0

        for c in campaigns:
            st = c.get("상태", "")
            status_counts[st] = status_counts.get(st, 0) + 1
            cid = c.get("캠페인ID", "")
            if cid in stats:
                s = stats[cid]
                total_active += s.get("active", 0)
                total_done += s.get("done", 0)
                total_today += s.get("today", 0)

        return {
            "ok": True,
            "total_campaigns": len(campaigns),
            "status_counts": status_counts,
            "active_applications": total_active,
            "completed": total_done,
            "today": total_today
        }

    def _do_list_clients(self, portal: str, owner_id) -> dict:
        """업체 목록"""
        db = models.db_manager
        if not db:
            return {"ok": False, "error": "DB 연결 없음"}
        try:
            if portal == "agency" and owner_id:
                clients = db._fetchall(
                    "SELECT id, company_name, login_id FROM clients WHERE agency_id = %s ORDER BY company_name",
                    (owner_id,)
                )
            else:
                clients = db._fetchall("SELECT id, company_name, login_id FROM clients ORDER BY company_name")
            result = [{"id": c["id"], "업체명": c["company_name"], "로그인ID": c["login_id"]} for c in clients]
            return {"ok": True, "clients": result, "total": len(result)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _do_list_agencies(self) -> dict:
        """대행사 목록"""
        db = models.db_manager
        if not db:
            return {"ok": False, "error": "DB 연결 없음"}
        try:
            agencies = db._fetchall("SELECT id, company_name, login_id FROM agencies ORDER BY company_name")
            result = [{"id": a["id"], "대행사명": a["company_name"], "로그인ID": a["login_id"]} for a in agencies]
            return {"ok": True, "agencies": result, "total": len(result)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _get_filtered_campaigns(self, portal: str, owner_id) -> list:
        """포탈별 캠페인 필터링"""
        db = models.db_manager
        if portal == "admin":
            return db.get_all_campaigns()
        elif portal == "agency" and owner_id:
            return db.get_agency_campaigns(owner_id)
        elif portal == "client" and owner_id:
            return db.get_client_campaigns(owner_id)
        return []

    def _check_access(self, campaign: dict, portal: str, owner_id) -> bool:
        """캠페인 접근 권한 확인"""
        if portal == "admin":
            return True
        if portal == "agency" and owner_id:
            return str(campaign.get("대행사ID", "")) == str(owner_id)
        if portal == "client" and owner_id:
            return str(campaign.get("클라이언트ID", "")) == str(owner_id)
        return False


# Singleton
_instance = None

def get_chat_engine() -> AICampaignChat:
    """싱글턴 채팅 엔진 반환"""
    global _instance
    if _instance is None:
        _instance = AICampaignChat()
    return _instance

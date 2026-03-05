"""AI 대화형 캠페인 관리 엔진 (Claude Sonnet)"""
import csv
import io
import json
import logging
import os
import time
import uuid
from anthropic import Anthropic

import models

logger = logging.getLogger(__name__)

# CSV 내보내기 임시 저장소 (토큰 → {data, filename, created})
_export_store = {}

def _cleanup_exports():
    """10분 이상 된 내보내기 파일 정리"""
    now = time.time()
    expired = [k for k, v in _export_store.items() if now - v["created"] > 600]
    for k in expired:
        del _export_store[k]

def get_export(token: str) -> dict | None:
    """토큰으로 내보내기 데이터 조회 (admin.py에서 사용)"""
    _cleanup_exports()
    return _export_store.pop(token, None)


SYSTEM_PROMPT = """당신은 카비서 캠페인 관리 도우미입니다.
캠페인 등록, 수정, 현황 조회를 대화형으로 처리합니다.

## 캠페인 필드

### ★ 기본 정보 (모두 필수 — 하나라도 빠지면 반드시 물어볼 것)
- 업체명(company): 텍스트
- 캠페인명(campaign_name): 텍스트
- 상품명(product_name): 텍스트
- 옵션(options): 텍스트 (없으면 "없음"으로 확인)
- 플랫폼(platform): "스마트스토어"|"쿠팡"|"오늘의집"|"11번가"|"지마켓"|"올리브영"|기타
- 캠페인유형(campaign_type): "실배송"|"빈박스" [기본: 실배송]
- 상품링크(product_link): URL
- 결제금액(payment_amount): 정수(원)
- 리뷰비(review_fee): 정수(원) [관리자 전용]
- 총수량(total_qty): 정수 (총 모집인원)
- 일수량(daily_qty): 정수 (하루진행량)
- 진행일수(duration_days): 정수
- 1인일일제한(max_per_person_daily): 정수 [0=무제한]
- 구매가능시간(buy_time): "HH:MM~HH:MM" 또는 "제한없음"
- 중복허용(allow_duplicate): "Y"|"N"|"텀:30"
- 키워드(keyword): 텍스트
- 유입방식(entry_method): "키워드검색"|"링크유입"|기타
- 리뷰기한일수(review_deadline_days): 정수
- 캠페인가이드(campaign_guide): 텍스트 (리뷰어에게 전달할 구매 가이드)
- 추가안내사항(extra_info): 텍스트 (구매 주의사항)

### 추가 설정 (선택)
- 상품이미지(product_image): URL
- 공개여부(is_public): "Y"|"N" [기본: Y]

## 캠페인 상태값
모집중, 진행중, 마감, 종료, 승인대기, 대행사승인, 반려, 임시저장

## 등록 규칙 (매우 중요 — 반드시 지킬 것)
1. **기본 정보 항목을 모두 확인할 때까지 절대 등록하지 마세요.** 하나라도 빠진 항목이 있으면 물어보세요.
2. 사용자가 여러 정보를 한번에 알려주면 받되, 아직 안 알려준 항목은 꼭 추가로 물어보세요.
3. 모든 기본 정보가 갖춰지면 **전체 항목을 요약 표시**하고 "등록할까요?" 확인을 받으세요.
4. 확인 받은 뒤에만 create_campaign을 호출하세요.
5. **등록 시 is_public은 항상 "N"(비노출)으로 설정하세요.**
6. 등록 완료 후 "캠페인이 **비노출** 상태로 등록되었습니다. 내용 확인 후 캠페인 목록에서 노출로 변경해주세요." 라고 안내하세요.

## 상태 워크플로우
- 관리자 생성 → "모집중" (비노출)
- 대행사 생성 → "대행사승인"
- 클라이언트 생성 → "승인대기"

## 수정/조회 규칙
1. 수정 시 캠페인 ID 또는 이름으로 특정 → 변경 필드만 업데이트
2. 현황 요청 시 적절한 도구 호출 후 보기 좋게 정리
3. 한국어 대화, 간결하게
4. 이미지 URL이 전달되면 상품이미지로 활용

## 진행 데이터 (progress)
- 캠페인별 리뷰어의 신청/구매/리뷰 진행건
- 수정 가능 필드: 수취인명, 연락처, 은행, 계좌, 예금주, 결제금액, 주문번호, 상태, 비고, 리뷰비, 입금금액, 아이디, 닉네임, 주소, 구매일, 리뷰기한, 리뷰제출일
- 상태값: 신청, 가이드전달, 구매캡쳐대기, 리뷰대기, 리뷰제출, 입금대기, 입금완료, 타임아웃취소, 취소
- 삭제 전 반드시 사용자에게 확인 받을 것
- 검색: 캠페인ID, 상태, 리뷰어명/연락처/아이디로 필터링

## 엑셀 데이터 정리
- 사용자가 엑셀에서 복사해서 붙여넣은 텍스트를 캠페인 필드에 맞게 정리
- 탭/줄바꿈으로 구분된 데이터를 파싱하여 필드별로 매핑
- 정리된 결과를 보여주고, 등록할지 확인 받기

## 데이터 내보내기 (CSV 다운로드)
- 사용자가 데이터를 엑셀로 달라고 하면 export_data 도구를 사용
- 캠페인 목록 또는 진행건 데이터를 CSV 파일로 생성
- 응답에 다운로드 링크를 포함하여 안내 (예: "다운로드: /admin/api/ai-export/abc123")
- columns 파라미터로 원하는 컬럼만 선택 가능
- 사용자가 특정 조건을 지정하면 status, campaign_id, query 등으로 필터링
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
    },
    {
        "name": "search_progress",
        "description": "진행 데이터(progress)를 검색합니다. 캠페인ID, 상태, 리뷰어명/연락처/아이디로 필터링.",
        "input_schema": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "캠페인 ID로 필터"},
                "status": {"type": "string", "description": "상태 필터 (신청/가이드전달/구매캡쳐대기/리뷰대기/리뷰제출/입금대기/입금완료/타임아웃취소/취소)"},
                "query": {"type": "string", "description": "리뷰어명, 연락처, 아이디 검색어"},
                "page": {"type": "integer", "description": "페이지 번호 (기본 1)"},
                "per_page": {"type": "integer", "description": "페이지당 건수 (기본 20)"}
            }
        }
    },
    {
        "name": "update_progress",
        "description": "진행건의 특정 필드를 수정합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "progress_id": {"type": "integer", "description": "진행건 ID"},
                "field": {"type": "string", "description": "한국어 필드명 (상태, 비고, 결제금액, 수취인명, 연락처, 은행, 계좌, 예금주, 주문번호, 리뷰비, 입금금액, 아이디, 닉네임, 주소, 구매일, 리뷰기한, 리뷰제출일)"},
                "value": {"type": "string", "description": "변경할 값"}
            },
            "required": ["progress_id", "field", "value"]
        }
    },
    {
        "name": "delete_progress",
        "description": "진행건을 삭제합니다. 되돌릴 수 없으므로 반드시 사용자 확인 후 호출하세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "progress_id": {"type": "integer", "description": "삭제할 진행건 ID"}
            },
            "required": ["progress_id"]
        }
    },
    {
        "name": "export_data",
        "description": "요청된 데이터를 CSV 파일로 생성하여 다운로드 링크를 반환합니다. 캠페인 목록 또는 진행건 데이터를 내보낼 수 있습니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["campaigns", "progress"], "description": "내보낼 데이터 종류"},
                "campaign_id": {"type": "string", "description": "진행건 내보내기 시 캠페인 ID (선택)"},
                "status": {"type": "string", "description": "상태 필터 (선택)"},
                "query": {"type": "string", "description": "검색어 (선택)"},
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "내보낼 컬럼 목록 (한국어 필드명). 비우면 기본 컬럼 전체."
                }
            },
            "required": ["type"]
        }
    },
    {
        "name": "parse_excel_data",
        "description": "사용자가 붙여넣은 엑셀/텍스트 데이터를 캠페인 필드 형식으로 파싱합니다. 탭/줄바꿈 구분 데이터를 분석하여 정리된 결과를 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "raw_text": {"type": "string", "description": "탭/줄바꿈으로 구분된 원본 텍스트"}
            },
            "required": ["raw_text"]
        }
    }
]


class AICampaignChat:
    """AI 대화형 캠페인 관리 엔진"""

    def __init__(self):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다")
        self.client = Anthropic(api_key=api_key, timeout=120.0)
        self.model = "claude-sonnet-4-20250514"

    def _get_tools_for_portal(self, portal: str) -> list:
        """포탈별 사용 가능한 도구 필터링"""
        _ADMIN_ONLY = {"search_progress", "update_progress", "delete_progress", "parse_excel_data", "export_data"}
        if portal == "admin":
            return TOOLS  # 전체 접근
        elif portal == "agency":
            # 대행사: 클라이언트 목록, 캠페인 CRUD, 대시보드 (진행데이터/엑셀 제외)
            exclude = {"list_agencies"} | _ADMIN_ONLY
            return [t for t in TOOLS if t["name"] not in exclude]
        else:  # client
            # 클라이언트: 본인 캠페인만 (진행데이터/엑셀 제외)
            exclude = {"list_agencies", "list_clients"} | _ADMIN_ONLY
            return [t for t in TOOLS if t["name"] not in exclude]

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
        if depth > 3:
            # 중간 텍스트가 있으면 그것이라도 반환
            text_parts = []
            if response and response.content:
                text_parts = [b.text for b in response.content if hasattr(b, 'text')]
            reply = "\n".join(text_parts) if text_parts else "처리가 길어지고 있습니다. 질문을 더 구체적으로 해주세요."
            return {"reply": reply, "messages": messages}

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
            elif name == "search_progress":
                return self._do_search_progress(input_data)
            elif name == "update_progress":
                return self._do_update_progress(input_data)
            elif name == "delete_progress":
                return self._do_delete_progress(input_data)
            elif name == "export_data":
                return self._do_export_data(input_data, portal, owner_id)
            elif name == "parse_excel_data":
                return {"ok": True, "raw_text": input_data.get("raw_text", ""),
                        "hint": "이 텍스트를 분석하여 캠페인 필드에 매핑하세요. 헤더행이 있으면 활용하고, 없으면 값 패턴으로 추론하세요."}
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
        # 항상 비노출로 등록 (관리자가 확인 후 노출 전환)
        campaign_data["공개여부"] = "N"
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
            # 대행사: 승인 전 상태만 수정 가능
            status = campaign.get("상태", "")
            if status not in ("대행사승인", "반려", "임시저장"):
                return {"ok": False, "error": f"현재 상태({status})에서는 수정할 수 없습니다. 관리자에게 요청해주세요."}
            changes.pop("리뷰비", None)
        elif portal == "client" and owner_id:
            if str(campaign.get("클라이언트ID", "")) != str(owner_id):
                return {"ok": False, "error": "접근 권한이 없습니다"}
            # 클라이언트: 승인 전 상태만 수정 가능
            status = campaign.get("상태", "")
            if status not in ("승인대기", "반려", "임시저장"):
                return {"ok": False, "error": f"현재 상태({status})에서는 수정할 수 없습니다. 관리자에게 요청해주세요."}
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

    def _do_export_data(self, data: dict, portal: str, owner_id) -> dict:
        """데이터를 CSV로 생성하고 다운로드 토큰 반환"""
        db = models.db_manager
        if not db:
            return {"ok": False, "error": "DB 연결 없음"}

        export_type = data.get("type", "")
        custom_columns = data.get("columns", [])

        output = io.StringIO()
        output.write('\ufeff')  # UTF-8 BOM
        writer = csv.writer(output)

        row_count = 0

        if export_type == "campaigns":
            columns = custom_columns or [
                "캠페인ID", "캠페인명", "상품명", "업체명", "플랫폼", "캠페인유형",
                "상태", "총수량", "일수량", "결제금액", "리뷰비",
                "구매가능시간", "키워드", "유입방식", "공개여부", "중복허용", "등록일",
            ]
            writer.writerow(columns)
            campaigns = self._get_filtered_campaigns(portal, owner_id)
            status_filter = data.get("status", "")
            if status_filter:
                campaigns = [c for c in campaigns if c.get("상태") == status_filter]
            for c in campaigns:
                writer.writerow([c.get(col, "") for col in columns])
            row_count = len(campaigns)
            filename = "campaigns"

        elif export_type == "progress":
            columns = custom_columns or [
                "id", "캠페인ID", "제품명", "진행자이름", "진행자연락처",
                "수취인명", "연락처", "아이디", "상태", "결제금액",
                "리뷰비", "입금금액", "주문번호", "비고", "날짜",
            ]
            writer.writerow(columns)
            campaign_id = data.get("campaign_id", "")
            status = data.get("status", "")
            query = data.get("query", "")
            items, total = db.get_progress_page(1, 9999, campaign_id, status, query)
            for it in items:
                writer.writerow([it.get(col, "") for col in columns])
            row_count = total
            filename = "progress"
        else:
            return {"ok": False, "error": f"알 수 없는 내보내기 유형: {export_type}"}

        # 임시 저장소에 보관
        token = uuid.uuid4().hex[:12]
        _export_store[token] = {
            "data": output.getvalue(),
            "filename": f"{filename}_{int(time.time())}.csv",
            "created": time.time(),
        }
        _cleanup_exports()

        return {
            "ok": True,
            "download_url": f"/admin/api/ai-export/{token}",
            "message": f"CSV 파일이 생성되었습니다 ({row_count}건). 아래 링크를 클릭하여 다운로드하세요.",
            "total_rows": row_count,
        }

    def _do_search_progress(self, data: dict) -> dict:
        """진행건 검색"""
        db = models.db_manager
        if not db:
            return {"ok": False, "error": "DB 연결 없음"}
        campaign_id = data.get("campaign_id", "")
        status = data.get("status", "")
        query = data.get("query", "")
        page = data.get("page", 1) or 1
        per_page = min(data.get("per_page", 20) or 20, 50)
        items, total = db.get_progress_page(page, per_page, campaign_id, status, query)
        # 간략화: 주요 필드만 반환
        brief = []
        for it in items:
            brief.append({
                "id": it.get("id"),
                "캠페인ID": it.get("캠페인ID", ""),
                "제품명": it.get("제품명", ""),
                "진행자이름": it.get("진행자이름", ""),
                "진행자연락처": it.get("진행자연락처", ""),
                "아이디": it.get("아이디", ""),
                "상태": it.get("상태", ""),
                "결제금액": it.get("결제금액", ""),
                "리뷰비": it.get("리뷰비", ""),
                "입금금액": it.get("입금금액", ""),
                "비고": it.get("비고", ""),
                "날짜": it.get("날짜", ""),
            })
        return {"ok": True, "items": brief, "total": total, "page": page, "per_page": per_page}

    def _do_update_progress(self, data: dict) -> dict:
        """진행건 필드 수정"""
        db = models.db_manager
        if not db:
            return {"ok": False, "error": "DB 연결 없음"}
        progress_id = data.get("progress_id")
        field = data.get("field", "")
        value = data.get("value", "")
        if not progress_id or not field:
            return {"ok": False, "error": "progress_id와 field가 필요합니다"}
        try:
            db.update_progress_field(int(progress_id), field, value)
            return {"ok": True, "message": f"진행건 {progress_id}의 {field}을(를) '{value}'로 변경했습니다."}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _do_delete_progress(self, data: dict) -> dict:
        """진행건 삭제"""
        db = models.db_manager
        if not db:
            return {"ok": False, "error": "DB 연결 없음"}
        progress_id = data.get("progress_id")
        if not progress_id:
            return {"ok": False, "error": "progress_id가 필요합니다"}
        try:
            result = db.delete_progress(int(progress_id))
            if result:
                return {"ok": True, "message": f"진행건 {progress_id} 삭제 완료"}
            return {"ok": False, "error": f"진행건 {progress_id}을(를) 찾을 수 없습니다"}
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

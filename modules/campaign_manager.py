"""
campaign_manager.py - 캠페인 관리

캠페인 조회, 등록, 수정, 마감, 모집글 생성 등.
"""

import logging
from modules.utils import today_str, safe_int, is_within_buy_time

logger = logging.getLogger(__name__)

RECRUIT_TEMPLATE = """📢 체험단 모집

{product_name}
💰 결제금액: {product_price}원
📦 {campaign_type}
👥 {total}명 모집 (남은 {remaining}자리)

👉 신청하기
{web_url}"""


class CampaignManager:
    """캠페인 관리 매니저"""

    def __init__(self, db):
        self.db = db

    def get_active_campaigns(self) -> list[dict]:
        """모집 중인 캠페인 목록"""
        all_campaigns = self.db.get_all_campaigns()

        # 실제 신청 건수
        actual_counts = {}
        try:
            actual_counts = self.db.count_all_campaigns()
        except Exception:
            pass

        # 오늘 신청 건수
        today_counts = {}
        try:
            today_counts = self.db.count_today_all_campaigns()
        except Exception:
            pass

        active = []
        for c in all_campaigns:
            status = c.get("상태", "")
            # 비공개 캠페인 제외
            if c.get("공개여부", "").strip().upper() in ("N",):
                continue
            if status in ("모집중", "진행중", ""):
                total = safe_int(c.get("총수량", 0))
                campaign_id = c.get("캠페인ID", "")
                # 실제 신청 건수 우선, 없으면 완료수량
                done = actual_counts.get(campaign_id, 0) or safe_int(c.get("완료수량", 0))
                total_remaining = total - done
                if total_remaining > 0:
                    # 일일 목표가 있으면 일일 잔여 기준
                    daily_target = self._get_today_target(c)
                    if daily_target > 0:
                        today_done = today_counts.get(campaign_id, 0)
                        remaining = min(total_remaining, daily_target - today_done)
                    else:
                        remaining = total_remaining
                    if remaining <= 0:
                        continue
                    c["_남은수량"] = remaining
                    c["_완료수량"] = done
                    c["_buy_time_active"] = is_within_buy_time(c.get("구매가능시간", ""))
                    active.append(c)
        return active

    def get_campaign_by_index(self, index: int) -> dict | None:
        """활성 캠페인 중 index 번째 (1-based)"""
        active = self.get_active_campaigns()
        if 1 <= index <= len(active):
            return active[index - 1]
        return None

    def get_campaign_by_id(self, campaign_id: str) -> dict | None:
        return self.db.get_campaign_by_id(campaign_id)

    def get_all_campaigns(self) -> list[dict]:
        return self.db.get_all_campaigns()

    def build_campaign_cards(self, name: str = "", phone: str = "") -> list[dict]:
        """채팅용 캠페인 카드 데이터 (chat.js에서 렌더링)

        모집중 캠페인 + 마감 캠페인 모두 표시.
        마감 캠페인은 closed=True로 표시하되 신청 불가.
        """
        import re
        all_campaigns = self.db.get_all_campaigns()
        if not all_campaigns:
            return []

        # 실제 신청 건수
        actual_counts = {}
        try:
            actual_counts = self.db.count_all_campaigns()
        except Exception:
            pass

        # 리뷰어 이력 조회
        reviewer_items = []
        if name and phone:
            try:
                reviewer_items = self.db.search_by_name_phone(name, phone)
            except Exception:
                pass

        # 오늘 캠페인별 진행 건수
        today_counts = {}
        try:
            today_counts = self.db.count_today_all_campaigns()
        except Exception:
            pass

        cards = []
        card_index = 0
        for c in all_campaigns:
            campaign_status = c.get("상태", "")
            # 비공개 캠페인 제외
            if c.get("공개여부", "").strip().upper() in ("N",):
                continue

            total = safe_int(c.get("총수량", 0))
            campaign_id = c.get("캠페인ID", "")
            done = actual_counts.get(campaign_id, 0) or safe_int(c.get("완료수량", 0))
            total_remaining = total - done

            # 모집중이 아닌 캠페인(중지/마감 등)은 목록에서 제외
            if campaign_status not in ("모집중", "진행중", ""):
                continue

            # 마감 판단 (모집중이지만 잔여수량 0)
            is_closed = False
            closed_reason = ""
            if total_remaining <= 0:
                is_closed = True
                closed_reason = "마감"

            # 금일 마감 체크
            daily_target = self._get_today_target(c)
            today_done = today_counts.get(campaign_id, 0)
            daily_full = daily_target > 0 and today_done >= daily_target
            if not is_closed and daily_full:
                is_closed = True
                closed_reason = "금일마감"

            buy_time_str = c.get("구매가능시간", "").strip()
            buy_time_active = is_within_buy_time(buy_time_str)

            # 상세 정보
            product_price = c.get("상품금액", "") or c.get("결제금액", "")
            review_fee = c.get("리뷰비", "") or ""
            platform = c.get("플랫폼", "") or c.get("캠페인유형", "") or ""

            # active 캠페인만 인덱스 부여 (get_campaign_by_index와 일치)
            if not is_closed:
                card_index += 1
            card_value = f"campaign_{card_index}" if not is_closed else ""
            # 실효 잔여: 일일목표가 있으면 일일 잔여와 총 잔여 중 작은 값
            if daily_target > 0:
                daily_remaining = daily_target - today_done
                remaining = min(total_remaining, daily_remaining)
            else:
                remaining = total_remaining

            card = {
                "value": card_value,
                "name": c.get("캠페인명", "") or c.get("상품명", ""),
                "store": c.get("업체명", ""),
                "total": total,
                "remaining": max(remaining, 0),
                "daily_target": daily_target,
                "today_done": today_done,
                "daily_full": daily_full,
                "urgent": not is_closed and remaining <= 5,
                "buy_time": buy_time_str or "",
                "buy_time_closed": not buy_time_active,
                "product_price": str(product_price),
                "review_fee": str(review_fee),
                "platform": str(platform),
                "closed": is_closed,
                "closed_reason": closed_reason,
            }

            # 이 캠페인에서의 내 진행 이력
            if campaign_id and reviewer_items:
                my_history = []
                for item in reviewer_items:
                    if item.get("캠페인ID") == campaign_id:
                        sid = item.get("아이디", "").strip()
                        status = item.get("상태", "")
                        if sid and status not in ("타임아웃취소", "취소"):
                            my_history.append({"id": sid, "status": status})
                if my_history:
                    card["my_history"] = my_history

            cards.append(card)

        # 활성 캠페인 먼저, 마감 캠페인 뒤로
        cards.sort(key=lambda x: (x["closed"], x["name"]))
        return cards

    def _get_today_target(self, campaign: dict) -> int:
        """오늘 목표 수량. 일정이 있으면 해당 날짜 목표, 없으면 일수량 최대값."""
        import re
        from datetime import datetime
        from modules.utils import now_kst

        schedule = campaign.get("일정", [])
        start_date_str = campaign.get("시작일", "").strip()

        if schedule and start_date_str:
            try:
                start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                day_index = (now_kst().date() - start).days
                if 0 <= day_index < len(schedule):
                    return safe_int(schedule[day_index])
                elif day_index >= len(schedule):
                    return 0  # 일정 종료
            except Exception:
                pass

        # 폴백: 일수량 최대값
        daily_str = campaign.get("일수량", "").strip()
        if daily_str:
            range_match = re.match(r"(\d+)\s*[-~]\s*(\d+)", daily_str)
            if range_match:
                return safe_int(range_match.group(2))
            return safe_int(daily_str)
        return 0

    def build_campaign_list_text(self, name: str = "", phone: str = "") -> str:
        """채팅용 캠페인 목록 텍스트 (하위호환)"""
        from modules.response_templates import (
            CAMPAIGN_LIST_HEADER, CAMPAIGN_ITEM, CAMPAIGN_ITEM_WITH_IDS,
            CAMPAIGN_LIST_FOOTER, NO_CAMPAIGNS
        )

        active = self.get_active_campaigns()
        if not active:
            return NO_CAMPAIGNS

        text = CAMPAIGN_LIST_HEADER
        for i, c in enumerate(active, 1):
            total = safe_int(c.get("총수량", 0))
            done = safe_int(c.get("완료수량", 0))
            remaining = c.get("_남은수량", total - done)
            review_fee = c.get("리뷰비", "") or "미정"
            campaign_id = c.get("캠페인ID", "")

            my_ids = []
            if name and phone and campaign_id:
                try:
                    my_ids = self.db.get_user_campaign_ids(name, phone, campaign_id)
                except Exception:
                    pass

            display_name = c.get("캠페인명", "") or c.get("상품명", "")
            if my_ids:
                text += CAMPAIGN_ITEM_WITH_IDS.format(
                    idx=i,
                    product_name=display_name,
                    store_name=c.get("업체명", ""),
                    option=c.get("옵션", "없음"),
                    remaining=remaining,
                    review_fee=review_fee,
                    my_ids=", ".join(my_ids),
                )
            else:
                text += CAMPAIGN_ITEM.format(
                    idx=i,
                    product_name=display_name,
                    store_name=c.get("업체명", ""),
                    option=c.get("옵션", "없음"),
                    remaining=remaining,
                    review_fee=review_fee,
                )
        text += CAMPAIGN_LIST_FOOTER
        return text

    def build_recruit_message(self, campaign: dict, web_url: str) -> str:
        """모집글 생성"""
        total = safe_int(campaign.get("총수량", 0))
        done = safe_int(campaign.get("완료수량", 0))
        remaining = campaign.get("_남은수량", total - done)

        product_price = campaign.get("상품금액", "") or campaign.get("결제금액", "")
        if not product_price:
            product_price = "확인필요"

        campaign_type = campaign.get("캠페인유형", "").strip()
        if campaign_type == "빈박스":
            campaign_type = "빈박스"
        elif campaign_type == "실배송":
            campaign_type = "실배송"
        else:
            campaign_type = "실배송"

        return RECRUIT_TEMPLATE.format(
            product_name=campaign.get("캠페인명", "") or campaign.get("상품명", ""),
            product_price=product_price,
            campaign_type=campaign_type,
            total=total,
            remaining=remaining,
            web_url=web_url,
        ).strip()

    def get_needs_recruit(self, web_url: str) -> dict:
        """홍보가 필요한 캠페인 목록 + 통합 모집글.
        Returns: {"campaigns": [...], "combined_message": str|None}
        """
        active = self.get_active_campaigns()
        today_counts = {}
        try:
            today_counts = self.db.count_today_all_campaigns()
        except Exception:
            pass

        result = []
        for c in active:
            promo_enabled = c.get("홍보활성", "")
            if promo_enabled == "N":
                continue

            daily_target = self._get_today_target(c)
            campaign_id = c.get("캠페인ID", "")
            if daily_target > 0 and today_counts.get(campaign_id, 0) >= daily_target:
                continue

            if not c.get("_buy_time_active", True):
                continue

            promo_start = (c.get("홍보시작시간") or "").strip()
            promo_end = (c.get("홍보종료시간") or "").strip()
            if promo_enabled == "Y" and promo_start and promo_end:
                from modules.utils import now_kst
                now_hm = now_kst().strftime("%H:%M")
                if not (promo_start <= now_hm < promo_end):
                    continue

            # 한줄멘트: 홍보메시지 → 상품명 fallback
            oneliner = (c.get("홍보메시지") or "").strip()
            if not oneliner:
                oneliner = c.get("캠페인명", "") or c.get("상품명", "")
            c["_oneliner"] = oneliner

            c["_promo_enabled"] = promo_enabled == "Y"
            c["_promo_categories"] = (c.get("홍보카테고리") or "").strip()
            c["_promo_start"] = promo_start or "09:00"
            c["_promo_end"] = promo_end or "22:00"
            c["_promo_cooldown"] = safe_int(c.get("홍보주기", 60)) or 60

            result.append(c)

        combined = self.build_combined_recruit_message(result)
        return {"campaigns": result, "combined_message": combined}

    def build_combined_recruit_message(self, campaigns: list[dict]) -> str | None:
        """활성 캠페인들을 하나의 통합 홍보 메시지로 조합."""
        if not campaigns:
            return None

        header = self.db.get_setting("promo_header", "리뷰 진행 체험단/기자단 모집합니다!")
        footer = self.db.get_setting("promo_footer", "많은 지원 부탁드려요:)")
        link = self.db.get_setting("promo_link", "https://kabiseo.com/campaigns")

        lines = []
        for c in campaigns:
            oneliner = c.get("_oneliner", c.get("캠페인명", ""))
            lines.append(f"🔹 {oneliner}")

        body = "\n".join(lines)
        parts = [header, "", body, "", footer]
        if link:
            parts.append(f"👉 {link}")

        return "\n".join(parts)

    def is_daily_full(self, campaign: dict) -> bool:
        """해당 캠페인의 금일 모집목표 도달 여부"""
        daily_target = self._get_today_target(campaign)
        if daily_target <= 0:
            return False
        campaign_id = campaign.get("캠페인ID", "")
        if not campaign_id:
            return False
        try:
            counts = self.db.count_today_all_campaigns()
            return counts.get(campaign_id, 0) >= daily_target
        except Exception:
            return False

    def check_capacity(self, campaign_id: str, add_count: int = 1) -> int:
        """정원 여유 확인. 남은 슬롯 수 반환 (0이면 꽉 참)"""
        campaign = self.get_campaign_by_id(campaign_id)
        if not campaign:
            return 0
        total = safe_int(campaign.get("총수량", 0))
        reserved = self.db.count_reserved_campaign(campaign_id)
        return max(0, total - reserved)

    def check_daily_remaining(self, campaign_id: str) -> int:
        """일일 모집 잔여 슬롯 수 반환. 일일 제한 없으면 -1 (무제한)."""
        campaign = self.get_campaign_by_id(campaign_id)
        if not campaign:
            return 0
        daily_target = self._get_today_target(campaign)
        if daily_target <= 0:
            return -1  # 일일 제한 없음
        try:
            counts = self.db.count_today_all_campaigns()
            today_count = counts.get(campaign_id, 0)
            return max(0, daily_target - today_count)
        except Exception:
            return -1

    def get_campaign_stats(self, campaign_id: str) -> dict:
        """캠페인 달성률"""
        campaign = self.get_campaign_by_id(campaign_id)
        if not campaign:
            return {}
        total = safe_int(campaign.get("총수량", 0))
        done = safe_int(campaign.get("완료수량", 0))
        remaining = total - done
        rate = (done / total * 100) if total > 0 else 0
        return {
            "campaign_id": campaign_id,
            "total": total,
            "recruited": done,
            "remaining": remaining,
            "rate": round(rate, 1),
        }

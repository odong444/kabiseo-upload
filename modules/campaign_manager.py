"""
campaign_manager.py - 캠페인 관리

캠페인 조회, 등록, 수정, 마감, 모집글 생성 등.
"""

import logging
from modules.utils import today_str, safe_int

logger = logging.getLogger(__name__)

RECRUIT_TEMPLATE = """📢 리뷰 체험단 모집 📢

✨ {product_name} ✨
🏪 {store_name}
{method_line}

💰 상품금액: {product_price}원
👥 남은 {remaining}명
{review_line}
{weekend_line}
👉 아래 링크에서 신청해주세요!
🔗 {web_url}

#리뷰체험단 #블로그체험단"""


class CampaignManager:
    """캠페인 관리 매니저"""

    def __init__(self, sheets_manager):
        self.sheets = sheets_manager

    def get_active_campaigns(self) -> list[dict]:
        """모집 중인 캠페인 목록"""
        all_campaigns = self.sheets.get_all_campaigns()
        active = []
        for c in all_campaigns:
            status = c.get("상태", "")
            # 비공개 캠페인 제외
            if c.get("공개여부", "").strip().upper() in ("N",):
                continue
            if status in ("모집중", "진행중", ""):
                total = safe_int(c.get("총수량", 0))
                done = safe_int(c.get("완료수량", 0))
                remaining = total - done
                if remaining > 0:
                    c["_남은수량"] = remaining
                    active.append(c)
        return active

    def get_campaign_by_index(self, index: int) -> dict | None:
        """활성 캠페인 중 index 번째 (1-based)"""
        active = self.get_active_campaigns()
        if 1 <= index <= len(active):
            return active[index - 1]
        return None

    def get_campaign_by_id(self, campaign_id: str) -> dict | None:
        return self.sheets.get_campaign_by_id(campaign_id)

    def get_all_campaigns(self) -> list[dict]:
        return self.sheets.get_all_campaigns()

    def build_campaign_cards(self, name: str = "", phone: str = "") -> list[dict]:
        """채팅용 캠페인 카드 데이터 (chat.js에서 렌더링)"""
        active = self.get_active_campaigns()
        if not active:
            return []

        # 리뷰어 이력 조회
        reviewer_items = []
        if name and phone:
            try:
                reviewer_items = self.sheets.search_by_name_phone(name, phone)
            except Exception:
                pass

        cards = []
        for i, c in enumerate(active, 1):
            total = safe_int(c.get("총수량", 0))
            done = safe_int(c.get("완료수량", 0))
            remaining = c.get("_남은수량", total - done)
            method = c.get("유입방식", "")
            campaign_id = c.get("캠페인ID", "")

            card = {
                "value": f"campaign_{i}",
                "name": c.get("상품명", ""),
                "store": c.get("업체명", ""),
                "method": method or "미정",
                "remaining": remaining,
                "urgent": remaining <= 5,
            }

            # 이 캠페인에서의 내 진행 이력
            if campaign_id and reviewer_items:
                my_history = []
                for item in reviewer_items:
                    if item.get("캠페인ID") == campaign_id:
                        sid = item.get("아이디", "").strip()
                        status = item.get("상태", "")
                        if sid:
                            my_history.append({"id": sid, "status": status})
                if my_history:
                    card["my_history"] = my_history

            cards.append(card)
        return cards

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
                    my_ids = self.sheets.get_user_campaign_ids(name, phone, campaign_id)
                except Exception:
                    pass

            if my_ids:
                text += CAMPAIGN_ITEM_WITH_IDS.format(
                    idx=i,
                    product_name=c.get("상품명", ""),
                    store_name=c.get("업체명", ""),
                    option=c.get("옵션", "없음"),
                    remaining=remaining,
                    review_fee=review_fee,
                    my_ids=", ".join(my_ids),
                )
            else:
                text += CAMPAIGN_ITEM.format(
                    idx=i,
                    product_name=c.get("상품명", ""),
                    store_name=c.get("업체명", ""),
                    option=c.get("옵션", "없음"),
                    remaining=remaining,
                    review_fee=review_fee,
                )
        text += CAMPAIGN_LIST_FOOTER
        return text

    def build_recruit_message(self, campaign: dict, web_url: str) -> str:
        """모집글 생성 (개선)"""
        total = safe_int(campaign.get("총수량", 0))
        done = safe_int(campaign.get("완료수량", 0))
        remaining = campaign.get("_남은수량", total - done)

        method = campaign.get("유입방식", "")
        if "키워드" in method:
            method_line = "🔍 키워드 유입"
        elif "링크" in method:
            method_line = "🔗 링크 유입"
        else:
            method_line = f"✅ 유입: {method}" if method else ""

        # 리뷰 타입
        review_type = campaign.get("리뷰타입", "") or campaign.get("리뷰제공", "")
        if review_type:
            review_line = f"📝 리뷰: {review_type}"
        else:
            review_line = ""

        # 주말작업
        weekend = campaign.get("주말작업", "").strip().upper()
        weekend_line = "✅ 주말 작업 가능" if weekend in ("Y", "O", "예") else ""

        # 상품금액
        product_price = campaign.get("상품금액", "") or campaign.get("결제금액", "")
        if not product_price:
            product_price = "확인필요"

        return RECRUIT_TEMPLATE.format(
            product_name=campaign.get("상품명", ""),
            store_name=campaign.get("업체명", ""),
            method_line=method_line,
            product_price=product_price,
            remaining=remaining,
            review_line=review_line,
            weekend_line=weekend_line,
            web_url=web_url,
        ).strip()

    def get_needs_recruit(self, web_url: str) -> list[dict]:
        """홍보가 필요한 캠페인 + 모집글"""
        active = self.get_active_campaigns()
        result = []
        for c in active:
            c["모집글"] = self.build_recruit_message(c, web_url)
            result.append(c)
        return result

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

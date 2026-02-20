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
📦 옵션: {option}

✅ 모집 인원: {remaining}명
✅ 유입 방식: {method}
✅ 리뷰: {review_type}

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

    def build_campaign_list_text(self) -> str:
        """채팅용 캠페인 목록 텍스트"""
        from modules.response_templates import (
            CAMPAIGN_LIST_HEADER, CAMPAIGN_ITEM, CAMPAIGN_LIST_FOOTER, NO_CAMPAIGNS
        )

        active = self.get_active_campaigns()
        if not active:
            return NO_CAMPAIGNS

        text = CAMPAIGN_LIST_HEADER
        for i, c in enumerate(active, 1):
            total = safe_int(c.get("총수량", 0))
            done = safe_int(c.get("완료수량", 0))
            remaining = c.get("_남은수량", total - done)
            text += CAMPAIGN_ITEM.format(
                idx=i,
                product_name=c.get("상품명", ""),
                store_name=c.get("업체명", ""),
                option=c.get("옵션", "없음"),
                remaining=remaining,
                price=c.get("결제금액", "?"),
            )
        text += CAMPAIGN_LIST_FOOTER
        return text

    def build_recruit_message(self, campaign: dict, web_url: str) -> str:
        """모집글 생성"""
        total = safe_int(campaign.get("총수량", 0))
        done = safe_int(campaign.get("완료수량", 0))
        remaining = campaign.get("_남은수량", total - done)
        return RECRUIT_TEMPLATE.format(
            product_name=campaign.get("상품명", ""),
            store_name=campaign.get("업체명", ""),
            option=campaign.get("옵션", "없음"),
            remaining=remaining,
            method=campaign.get("유입방식", ""),
            review_type=campaign.get("리뷰제공", ""),
            web_url=web_url,
        )

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

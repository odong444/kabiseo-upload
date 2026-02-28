"use client"

import Link from "next/link"
import { Clock, ShoppingBag, Tag } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import type { Campaign } from "@/lib/types"
import { cn } from "@/lib/utils"

export function CampaignCard({ campaign }: { campaign: Campaign }) {
  const isClosed = campaign.closed || campaign.remaining <= 0
  const hasBuyTime = !campaign.buy_time_closed && campaign.buy_time

  return (
    <Link href={`/campaigns/${campaign.campaign_id}`} className="block">
      <Card className={cn(
        "transition-shadow hover:shadow-md",
        isClosed && "opacity-60"
      )}>
        <CardContent className="flex flex-col gap-3 p-4">
          <div className="flex items-start justify-between gap-2">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <Badge variant={isClosed ? "secondary" : "default"} className="shrink-0 text-xs">
                  {isClosed ? "마감" : `${campaign.remaining}건 남음`}
                </Badge>
                {campaign.urgent && !isClosed && (
                  <Badge variant="destructive" className="shrink-0 text-xs">긴급</Badge>
                )}
              </div>
              <h3 className="font-semibold text-foreground truncate">{campaign.name}</h3>
              <p className="text-sm text-muted-foreground truncate">{campaign.store}</p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              <ShoppingBag className="h-3.5 w-3.5" />
              {campaign.product_price}
            </span>
            <span className="inline-flex items-center gap-1">
              <Tag className="h-3.5 w-3.5" />
              {campaign.review_fee}
            </span>
            {hasBuyTime && (
              <span className="inline-flex items-center gap-1 text-warning-foreground">
                <Clock className="h-3.5 w-3.5" />
                {campaign.buy_time}
              </span>
            )}
            <Badge variant="outline" className="text-xs">{campaign.platform}</Badge>
          </div>

          {campaign.my_history && campaign.my_history.length > 0 && (
            <div className="border-t border-border pt-2 mt-1">
              <p className="text-xs text-muted-foreground">
                내 신청: {campaign.my_history.length}건
              </p>
            </div>
          )}
        </CardContent>
      </Card>
    </Link>
  )
}

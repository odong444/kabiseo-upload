"use client"

import { useState } from "react"
import useSWR from "swr"
import { apiClient } from "@/lib/api"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { RefreshCw, Search, Plus, Pause, Play, Trash2, Edit, ExternalLink } from "lucide-react"
import Link from "next/link"

interface AdminCampaign {
  campaign_id: string
  name: string
  store: string
  platform: string
  status: string
  total: number
  today_done: number
  today_target: number
  active_count: number
  purchase_done: number
  review_done: number
  settlement_done: number
  daily_closed: boolean
}

export default function AdminCampaignsPage() {
  const [search, setSearch] = useState("")

  const { data: campaigns, isLoading, mutate } = useSWR<AdminCampaign[]>(
    "/api/admin/campaigns",
    (url: string) => apiClient<AdminCampaign[]>(url)
  )

  const filtered = campaigns?.filter((c) => {
    if (!search) return true
    const q = search.toLowerCase()
    return c.name.toLowerCase().includes(q) || c.store.toLowerCase().includes(q) || c.campaign_id.toLowerCase().includes(q)
  })

  async function handleAction(campaignId: string, action: "pause" | "resume" | "delete") {
    try {
      await apiClient(`/api/admin/campaigns/${campaignId}/${action}`, { method: "POST" })
      mutate()
    } catch (err) {
      alert(err instanceof Error ? err.message : "작업 실패")
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">캠페인 관리</h1>
          <p className="text-sm text-muted-foreground">{campaigns?.length || 0}개 캠페인</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => mutate()}>
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            새로고침
          </Button>
          <Link href="/admin/campaigns/new">
            <Button size="sm">
              <Plus className="mr-1.5 h-3.5 w-3.5" />
              새 캠페인
            </Button>
          </Link>
        </div>
      </div>

      <div className="relative max-w-sm">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input placeholder="캠페인명, 업체명, ID 검색..." className="pl-9" value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>

      {isLoading ? (
        <div className="flex flex-col gap-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-20 animate-pulse rounded-xl bg-muted" />
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {filtered?.map((c) => (
            <Card key={c.campaign_id}>
              <CardContent className="p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1 flex-wrap">
                      <StatusBadge status={c.status} />
                      {c.daily_closed && <Badge variant="secondary" className="text-[10px]">당일마감</Badge>}
                      <Badge variant="outline" className="text-[10px]">{c.platform}</Badge>
                      <span className="text-xs text-muted-foreground">{c.campaign_id}</span>
                    </div>
                    <h3 className="font-semibold text-foreground truncate">{c.name}</h3>
                    <p className="text-sm text-muted-foreground truncate">{c.store}</p>

                    <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
                      <span>신청 <strong className="text-foreground">{c.active_count}</strong></span>
                      <span>구매 <strong className="text-foreground">{c.purchase_done}</strong></span>
                      <span>리뷰 <strong className="text-foreground">{c.review_done}</strong></span>
                      <span>정산 <strong className="text-foreground">{c.settlement_done}</strong></span>
                      <span>오늘 <strong className="text-foreground">{c.today_done}/{c.today_target || "-"}</strong></span>
                    </div>
                  </div>

                  <div className="flex shrink-0 items-center gap-1">
                    <Link href={`/admin/campaigns/${c.campaign_id}`}>
                      <Button variant="ghost" size="icon" className="h-8 w-8" title="편집">
                        <Edit className="h-3.5 w-3.5" />
                      </Button>
                    </Link>
                    {c.status === "중지" ? (
                      <Button variant="ghost" size="icon" className="h-8 w-8" title="재개" onClick={() => handleAction(c.campaign_id, "resume")}>
                        <Play className="h-3.5 w-3.5" />
                      </Button>
                    ) : (
                      <Button variant="ghost" size="icon" className="h-8 w-8" title="중지" onClick={() => handleAction(c.campaign_id, "pause")}>
                        <Pause className="h-3.5 w-3.5" />
                      </Button>
                    )}
                    <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" title="삭제" onClick={() => {
                      if (confirm(`"${c.name}" 캠페인을 삭제하시겠습니까?`)) handleAction(c.campaign_id, "delete")
                    }}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}

          {filtered?.length === 0 && (
            <div className="py-16 text-center text-sm text-muted-foreground">
              {search ? "검색 결과가 없습니다." : "등록된 캠페인이 없습니다."}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const config: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
    "모집중": "default",
    "진행중": "default",
    "중지": "destructive",
    "마감": "secondary",
    "완료": "secondary",
  }
  return <Badge variant={config[status] || "outline"}>{status || "대기"}</Badge>
}

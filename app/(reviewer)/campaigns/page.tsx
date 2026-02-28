"use client"

import useSWR from "swr"
import { useState } from "react"
import { useAuth } from "@/lib/auth"
import { apiClient } from "@/lib/api"
import { CampaignCard } from "@/components/campaign-card"
import { Input } from "@/components/ui/input"
import { Search, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import type { Campaign } from "@/lib/types"

export default function CampaignsPage() {
  const { user } = useAuth()
  const [search, setSearch] = useState("")

  const { data: campaigns, isLoading, mutate } = useSWR<Campaign[]>(
    user ? `/api/campaigns?name=${encodeURIComponent(user.name)}&phone=${user.phone}` : null,
    (url: string) => apiClient<Campaign[]>(url)
  )

  const filtered = campaigns?.filter((c) => {
    if (!search) return true
    const q = search.toLowerCase()
    return (
      c.name.toLowerCase().includes(q) ||
      c.store.toLowerCase().includes(q) ||
      c.platform.toLowerCase().includes(q)
    )
  })

  return (
    <div className="flex flex-col">
      <header className="sticky top-0 z-40 border-b border-border bg-card px-4 py-3">
        <div className="flex items-center justify-between mb-3">
          <h1 className="text-lg font-bold text-foreground">캠페인</h1>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => mutate()}
            aria-label="새로고침"
            className="h-8 w-8"
          >
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="캠페인, 스토어, 플랫폼 검색..."
            className="pl-9"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </header>

      <div className="flex flex-col gap-3 p-4">
        {isLoading ? (
          <div className="flex flex-col gap-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="h-28 animate-pulse rounded-xl bg-muted" />
            ))}
          </div>
        ) : filtered && filtered.length > 0 ? (
          filtered.map((campaign) => (
            <CampaignCard key={campaign.campaign_id} campaign={campaign} />
          ))
        ) : (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <Search className="mb-3 h-10 w-10 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              {search ? "검색 결과가 없습니다." : "현재 진행 중인 캠페인이 없습니다."}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

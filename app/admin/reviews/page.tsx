"use client"

import { useState } from "react"
import useSWR from "swr"
import { apiClient } from "@/lib/api"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { RefreshCw, Search, CheckCircle, XCircle, Eye, ExternalLink, Loader2 } from "lucide-react"

interface ReviewItem {
  id: string
  reviewer_name: string
  reviewer_phone: string
  store_id: string
  campaign_name: string
  product_name: string
  type: "purchase" | "review"
  status: "pending" | "approved" | "rejected"
  image_url: string
  submitted_at: string
  remark: string
}

type TabKey = "pending" | "approved" | "rejected"

const TABS: { key: TabKey; label: string }[] = [
  { key: "pending", label: "대기" },
  { key: "approved", label: "승인" },
  { key: "rejected", label: "반려" },
]

export default function AdminReviewsPage() {
  const [tab, setTab] = useState<TabKey>("pending")
  const [search, setSearch] = useState("")
  const [rejectTarget, setRejectTarget] = useState<string | null>(null)
  const [rejectReason, setRejectReason] = useState("")
  const [processing, setProcessing] = useState<string | null>(null)

  const { data: reviews, isLoading, mutate } = useSWR<ReviewItem[]>(
    `/api/admin/reviews?status=${tab}`,
    (url: string) => apiClient<ReviewItem[]>(url)
  )

  const filtered = reviews?.filter((r) => {
    if (!search) return true
    const q = search.toLowerCase()
    return (
      r.reviewer_name.toLowerCase().includes(q) ||
      r.store_id.toLowerCase().includes(q) ||
      r.campaign_name.toLowerCase().includes(q)
    )
  })

  async function approve(id: string) {
    setProcessing(id)
    try {
      await apiClient(`/api/admin/reviews/${id}/approve`, { method: "POST" })
      mutate()
    } catch (err) {
      alert(err instanceof Error ? err.message : "승인 실패")
    } finally {
      setProcessing(null)
    }
  }

  async function reject(id: string) {
    setProcessing(id)
    try {
      await apiClient(`/api/admin/reviews/${id}/reject`, {
        method: "POST",
        body: JSON.stringify({ reason: rejectReason }),
      })
      setRejectTarget(null)
      setRejectReason("")
      mutate()
    } catch (err) {
      alert(err instanceof Error ? err.message : "반려 실패")
    } finally {
      setProcessing(null)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">리뷰 검수</h1>
          <p className="text-sm text-muted-foreground">제출된 구매/리뷰 사진을 검수합니다.</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => mutate()}>
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          새로고침
        </Button>
      </div>

      <div className="flex items-center gap-3">
        <div className="flex rounded-lg bg-muted p-1">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`rounded-md px-4 py-1.5 text-sm font-medium transition-colors ${
                tab === t.key ? "bg-card text-foreground shadow-sm" : "text-muted-foreground"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input placeholder="검색..." className="pl-9" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
      </div>

      {isLoading ? (
        <div className="flex flex-col gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-24 animate-pulse rounded-xl bg-muted" />
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {filtered?.map((r) => (
            <Card key={r.id}>
              <CardContent className="p-4">
                <div className="flex gap-4">
                  {/* Image preview */}
                  {r.image_url && (
                    <a href={r.image_url} target="_blank" rel="noopener noreferrer" className="shrink-0">
                      <div className="relative h-20 w-20 overflow-hidden rounded-lg border bg-muted">
                        <img
                          src={r.image_url}
                          alt="제출 이미지"
                          className="h-full w-full object-cover"
                          crossOrigin="anonymous"
                        />
                        <div className="absolute inset-0 flex items-center justify-center bg-foreground/0 transition-colors hover:bg-foreground/20">
                          <Eye className="h-4 w-4 text-background opacity-0 transition-opacity group-hover:opacity-100" />
                        </div>
                      </div>
                    </a>
                  )}

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1 flex-wrap">
                      <Badge variant={r.type === "purchase" ? "default" : "secondary"}>
                        {r.type === "purchase" ? "구매" : "리뷰"}
                      </Badge>
                      <span className="text-sm font-medium text-foreground">{r.reviewer_name}</span>
                      <span className="text-xs text-muted-foreground">{r.store_id}</span>
                    </div>
                    <p className="text-sm text-foreground truncate">{r.campaign_name} - {r.product_name}</p>
                    <p className="text-xs text-muted-foreground">{r.submitted_at}</p>
                    {r.remark && (
                      <p className="mt-1 text-xs text-destructive">{r.remark}</p>
                    )}
                  </div>

                  {tab === "pending" && (
                    <div className="flex shrink-0 flex-col gap-1">
                      <Button
                        size="sm"
                        onClick={() => approve(r.id)}
                        disabled={processing === r.id}
                      >
                        {processing === r.id ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <CheckCircle className="mr-1 h-3 w-3" />}
                        승인
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setRejectTarget(r.id)}
                        disabled={processing === r.id}
                      >
                        <XCircle className="mr-1 h-3 w-3" />
                        반려
                      </Button>
                    </div>
                  )}
                </div>

                {/* Reject dialog inline */}
                {rejectTarget === r.id && (
                  <div className="mt-3 flex items-end gap-2 border-t pt-3 border-border">
                    <div className="flex-1">
                      <Textarea
                        placeholder="반려 사유를 입력하세요..."
                        value={rejectReason}
                        onChange={(e) => setRejectReason(e.target.value)}
                        rows={2}
                      />
                    </div>
                    <div className="flex flex-col gap-1">
                      <Button size="sm" variant="destructive" onClick={() => reject(r.id)} disabled={!rejectReason.trim() || processing === r.id}>
                        반려
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => { setRejectTarget(null); setRejectReason("") }}>
                        취소
                      </Button>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          ))}

          {filtered?.length === 0 && (
            <div className="py-16 text-center text-sm text-muted-foreground">
              {tab === "pending" ? "대기 중인 검수 건이 없습니다." : "해당 항목이 없습니다."}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

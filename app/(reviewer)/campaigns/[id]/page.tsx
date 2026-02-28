"use client"

import { use, useState } from "react"
import useSWR from "swr"
import { useRouter } from "next/navigation"
import { useAuth } from "@/lib/auth"
import { apiClient } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  ArrowLeft, ExternalLink, ShoppingBag, Tag, Clock, Copy, Check, ChevronDown, ChevronUp,
} from "lucide-react"
import type { CampaignDetail, ApplyResult } from "@/lib/types"
import { cn } from "@/lib/utils"
import Link from "next/link"

export default function CampaignDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const { user } = useAuth()
  const router = useRouter()

  const { data: campaign, isLoading, mutate } = useSWR<CampaignDetail>(
    user ? `/api/campaigns/${id}?name=${encodeURIComponent(user.name)}&phone=${user.phone}` : null,
    (url: string) => apiClient<CampaignDetail>(url)
  )

  const [qty, setQty] = useState(1)
  const [applying, setApplying] = useState(false)
  const [applyResult, setApplyResult] = useState<ApplyResult | null>(null)
  const [copied, setCopied] = useState<string | null>(null)
  const [guideOpen, setGuideOpen] = useState(false)

  async function handleApply() {
    if (!user || !campaign) return
    setApplying(true)
    setApplyResult(null)
    try {
      const result = await apiClient<ApplyResult>(`/api/campaigns/${id}/apply`, {
        method: "POST",
        body: JSON.stringify({
          name: user.name,
          phone: user.phone,
          count: qty,
        }),
      })
      setApplyResult(result)
      mutate()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "신청 중 오류가 발생했습니다."
      setApplyResult({ ok: false, results: [], message })
    } finally {
      setApplying(false)
    }
  }

  function copyToClipboard(text: string, key: string) {
    navigator.clipboard.writeText(text)
    setCopied(key)
    setTimeout(() => setCopied(null), 1500)
  }

  if (isLoading) {
    return (
      <div className="flex flex-col gap-4 p-4">
        <div className="h-8 w-24 animate-pulse rounded-md bg-muted" />
        <div className="h-48 animate-pulse rounded-xl bg-muted" />
        <div className="h-32 animate-pulse rounded-xl bg-muted" />
      </div>
    )
  }

  if (!campaign) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <p className="text-muted-foreground">캠페인을 찾을 수 없습니다.</p>
        <Button variant="outline" className="mt-4" onClick={() => router.back()}>돌아가기</Button>
      </div>
    )
  }

  const isClosed = campaign.status === "closed" || campaign.remaining <= 0

  return (
    <div className="flex flex-col">
      <header className="sticky top-0 z-40 flex items-center gap-3 border-b border-border bg-card px-4 py-3">
        <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0" onClick={() => router.back()} aria-label="뒤로가기">
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <h1 className="truncate text-base font-semibold text-foreground">{campaign.name}</h1>
      </header>

      <div className="flex flex-col gap-4 p-4">
        {/* Basic info */}
        <Card>
          <CardContent className="flex flex-col gap-3 p-4">
            <div className="flex items-center gap-2 flex-wrap">
              <Badge variant={isClosed ? "secondary" : "default"}>
                {isClosed ? "마감" : `${campaign.remaining}건 남음`}
              </Badge>
              <Badge variant="outline">{campaign.platform}</Badge>
              {campaign.campaign_type && (
                <Badge variant="outline">{campaign.campaign_type}</Badge>
              )}
            </div>

            <h2 className="text-lg font-bold text-foreground">{campaign.product_name || campaign.name}</h2>
            <p className="text-sm text-muted-foreground">{campaign.store || campaign.name}</p>

            <div className="grid grid-cols-2 gap-3 mt-2">
              <InfoItem icon={<ShoppingBag className="h-4 w-4" />} label="상품가" value={String(campaign.product_price)} />
              <InfoItem icon={<Tag className="h-4 w-4" />} label="리뷰비" value={String(campaign.review_fee)} />
              {campaign.payment_amount && (
                <InfoItem icon={<Tag className="h-4 w-4" />} label="결제금액" value={String(campaign.payment_amount)} />
              )}
              {campaign.buy_time && (
                <InfoItem icon={<Clock className="h-4 w-4" />} label="구매시간" value={campaign.buy_time} />
              )}
            </div>

            {campaign.product_link && (
              <a
                href={campaign.product_link}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-sm font-medium text-primary-foreground bg-primary rounded-lg px-4 py-2.5 mt-2 transition-opacity hover:opacity-90"
              >
                <ExternalLink className="h-4 w-4" />
                상품 링크 열기
              </a>
            )}
          </CardContent>
        </Card>

        {/* Keyword & Options - copyable */}
        {(campaign.keyword || campaign.options) && (
          <Card>
            <CardHeader className="p-4 pb-2">
              <CardTitle className="text-sm">검색 키워드 / 옵션</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2 p-4 pt-0">
              {campaign.keyword && (
                <CopyRow label="키워드" value={campaign.keyword} copied={copied} onCopy={copyToClipboard} />
              )}
              {campaign.options && (
                <CopyRow label="옵션" value={campaign.options} copied={copied} onCopy={copyToClipboard} />
              )}
            </CardContent>
          </Card>
        )}

        {/* Guide sections */}
        {(campaign.campaign_guide || campaign.review_guide || campaign.extra_info) && (
          <Card>
            <button
              className="flex w-full items-center justify-between p-4 text-left"
              onClick={() => setGuideOpen(!guideOpen)}
            >
              <span className="text-sm font-semibold text-foreground">가이드 및 안내사항</span>
              {guideOpen ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
            </button>
            {guideOpen && (
              <CardContent className="flex flex-col gap-3 p-4 pt-0 border-t border-border">
                {campaign.campaign_guide && (
                  <GuideSection title="캠페인 가이드" content={campaign.campaign_guide} />
                )}
                {campaign.review_guide && (
                  <GuideSection title="리뷰 가이드" content={campaign.review_guide} />
                )}
                {campaign.extra_info && (
                  <GuideSection title="추가 안내" content={campaign.extra_info} />
                )}
                {campaign.dwell_time && (
                  <div className="text-sm"><span className="font-medium text-foreground">체류시간:</span> <span className="text-muted-foreground">{campaign.dwell_time}</span></div>
                )}
                {campaign.bookmark_required && (
                  <div className="text-sm"><span className="font-medium text-foreground">찜 필수:</span> <span className="text-muted-foreground">{campaign.bookmark_required}</span></div>
                )}
                {campaign.ship_memo_required && (
                  <div className="text-sm"><span className="font-medium text-foreground">배송메모:</span> <span className="text-muted-foreground">{campaign.ship_memo_content || campaign.ship_memo_required}</span></div>
                )}
              </CardContent>
            )}
          </Card>
        )}

        {/* Apply Section */}
        {!isClosed && (
          <Card className="border-primary/30 bg-primary/5">
            <CardContent className="p-4">
              <h3 className="text-sm font-semibold text-foreground mb-3">캠페인 신청</h3>
              <div className="flex items-end gap-3">
                <div className="flex-1">
                  <Label htmlFor="qty" className="text-xs text-muted-foreground">신청 수량</Label>
                  <Input
                    id="qty"
                    type="number"
                    min={1}
                    max={campaign.daily_remaining || campaign.remaining}
                    value={qty}
                    onChange={(e) => setQty(Math.max(1, Number(e.target.value)))}
                    className="mt-1"
                  />
                </div>
                <Button
                  onClick={handleApply}
                  disabled={applying || qty < 1}
                  className="min-w-24"
                >
                  {applying ? "신청 중..." : "신청하기"}
                </Button>
              </div>

              {applyResult && (
                <div className={cn(
                  "mt-3 rounded-lg p-3 text-sm",
                  applyResult.ok
                    ? "bg-success/10 text-success"
                    : "bg-destructive/10 text-destructive"
                )}>
                  <p>{applyResult.message}</p>
                  {applyResult.results.length > 0 && (
                    <ul className="mt-1 flex flex-col gap-1">
                      {applyResult.results.map((r, i) => (
                        <li key={i} className="flex items-center gap-2">
                          <span>{r.store_id}</span>
                          {r.ok ? (
                            <Badge variant="default" className="text-xs">성공</Badge>
                          ) : (
                            <span className="text-xs text-destructive">{r.error}</span>
                          )}
                          {r.progress_id && (
                            <Link href={`/task/${r.progress_id}`} className="text-xs underline">
                              작업 보기
                            </Link>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {/* My history */}
        {campaign.my_ids && campaign.my_ids.length > 0 && (
          <Card>
            <CardHeader className="p-4 pb-2">
              <CardTitle className="text-sm">내 신청 내역</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-2 p-4 pt-0">
              {campaign.my_ids.map((pid) => (
                <Link key={pid} href={`/task/${pid}`}>
                  <Badge variant="outline" className="cursor-pointer hover:bg-accent">
                    #{pid}
                  </Badge>
                </Link>
              ))}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}

function InfoItem({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg bg-muted p-2.5">
      <span className="text-muted-foreground">{icon}</span>
      <div className="min-w-0">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="text-sm font-medium text-foreground truncate">{value}</p>
      </div>
    </div>
  )
}

function CopyRow({
  label, value, copied, onCopy,
}: {
  label: string; value: string; copied: string | null; onCopy: (text: string, key: string) => void
}) {
  const isCopied = copied === label
  return (
    <div className="flex items-center justify-between rounded-lg bg-muted p-3">
      <div className="min-w-0 flex-1">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="text-sm font-medium text-foreground break-all">{value}</p>
      </div>
      <button
        onClick={() => onCopy(value, label)}
        className="ml-2 shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent"
        aria-label={`${label} 복사`}
      >
        {isCopied ? <Check className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
      </button>
    </div>
  )
}

function GuideSection({ title, content }: { title: string; content: string }) {
  return (
    <div>
      <h4 className="text-sm font-medium text-foreground mb-1">{title}</h4>
      <p className="whitespace-pre-wrap text-sm text-muted-foreground leading-relaxed">{content}</p>
    </div>
  )
}

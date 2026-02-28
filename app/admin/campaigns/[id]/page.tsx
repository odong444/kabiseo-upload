"use client"

import { useState, use } from "react"
import useSWR from "swr"
import { useRouter } from "next/navigation"
import { apiClient } from "@/lib/api"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { ArrowLeft, Save, Loader2 } from "lucide-react"
import Link from "next/link"

interface CampaignDetail {
  campaign_id: string
  name: string
  product_name: string
  store: string
  platform: string
  status: string
  product_price: string
  review_fee: string
  payment_amount: string
  total: string
  daily_amount: string
  schedule: string
  start_date: string
  buy_time: string
  product_link: string
  keyword: string
  review_deadline_days: string
  visibility: string
  guide: string
  memo: string
  ai_purchase_guide: string
  ai_review_guide: string
}

const FIELDS: { key: keyof CampaignDetail; label: string; type?: string; rows?: number }[] = [
  { key: "name", label: "캠페인명" },
  { key: "product_name", label: "상품명" },
  { key: "store", label: "업체명" },
  { key: "platform", label: "플랫폼" },
  { key: "status", label: "상태" },
  { key: "product_price", label: "상품금액" },
  { key: "review_fee", label: "리뷰비" },
  { key: "payment_amount", label: "결제금액" },
  { key: "total", label: "총수량" },
  { key: "daily_amount", label: "일수량" },
  { key: "start_date", label: "시작일", type: "date" },
  { key: "buy_time", label: "구매가능시간" },
  { key: "product_link", label: "상품링크" },
  { key: "keyword", label: "키워드" },
  { key: "review_deadline_days", label: "리뷰기한일수" },
  { key: "visibility", label: "공개여부" },
  { key: "guide", label: "캠페인가이드", rows: 4 },
  { key: "memo", label: "메모", rows: 2 },
  { key: "ai_purchase_guide", label: "AI구매검수지침", rows: 3 },
  { key: "ai_review_guide", label: "AI리뷰검수지침", rows: 3 },
]

export default function CampaignEditPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const router = useRouter()
  const [saving, setSaving] = useState(false)

  const { data, isLoading } = useSWR<CampaignDetail>(
    `/api/admin/campaigns/${id}`,
    (url: string) => apiClient<CampaignDetail>(url)
  )

  const [form, setForm] = useState<Partial<CampaignDetail>>({})

  // Merge fetched data with local form edits
  const merged = { ...data, ...form }

  function handleChange(key: keyof CampaignDetail, value: string) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  async function handleSave() {
    setSaving(true)
    try {
      await apiClient(`/api/admin/campaigns/${id}`, {
        method: "PUT",
        body: JSON.stringify(form),
      })
      router.push("/admin/campaigns")
    } catch (err) {
      alert(err instanceof Error ? err.message : "저장 실패")
    } finally {
      setSaving(false)
    }
  }

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6 max-w-3xl">
      <div className="flex items-center gap-3">
        <Link href="/admin/campaigns">
          <Button variant="ghost" size="icon" className="h-8 w-8">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        </Link>
        <div className="flex-1">
          <h1 className="text-2xl font-bold text-foreground">캠페인 편집</h1>
          <p className="text-sm text-muted-foreground">{id}</p>
        </div>
        <Button onClick={handleSave} disabled={saving}>
          {saving ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Save className="mr-1.5 h-3.5 w-3.5" />}
          저장
        </Button>
      </div>

      <Card>
        <CardHeader className="pb-4">
          <CardTitle className="text-base">캠페인 정보</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {FIELDS.map((f) => (
            <div key={f.key} className={f.rows ? "md:col-span-2" : ""}>
              <Label htmlFor={f.key} className="text-xs text-muted-foreground mb-1 block">{f.label}</Label>
              {f.rows ? (
                <Textarea
                  id={f.key}
                  rows={f.rows}
                  value={(merged[f.key] as string) || ""}
                  onChange={(e) => handleChange(f.key, e.target.value)}
                />
              ) : (
                <Input
                  id={f.key}
                  type={f.type || "text"}
                  value={(merged[f.key] as string) || ""}
                  onChange={(e) => handleChange(f.key, e.target.value)}
                />
              )}
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  )
}

"use client"

import { use, useState, useRef } from "react"
import useSWR from "swr"
import { useRouter } from "next/navigation"
import { useAuth } from "@/lib/auth"
import { apiClient } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { TaskStatusBadge } from "@/components/task-status-badge"
import {
  ArrowLeft, Upload, Copy, Check, ExternalLink, Camera, ChevronDown, ChevronUp, Image as ImageIcon,
} from "lucide-react"
import type { TaskDetail } from "@/lib/types"
import { cn } from "@/lib/utils"

export default function TaskDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const { user } = useAuth()
  const router = useRouter()

  const { data: task, isLoading, mutate } = useSWR<TaskDetail>(
    user ? `/api/task/${id}?name=${encodeURIComponent(user.name)}&phone=${user.phone}` : null,
    (url: string) => apiClient<TaskDetail>(url)
  )

  const [copied, setCopied] = useState<string | null>(null)
  const [guideOpen, setGuideOpen] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [uploadMsg, setUploadMsg] = useState("")
  const purchaseRef = useRef<HTMLInputElement>(null)
  const reviewRef = useRef<HTMLInputElement>(null)

  // Form state for purchase submission
  const [orderNumber, setOrderNumber] = useState("")
  const [recipientName, setRecipientName] = useState("")
  const [phone, setPhone] = useState("")
  const [address, setAddress] = useState("")
  const [bank, setBank] = useState("")
  const [account, setAccount] = useState("")
  const [depositor, setDepositor] = useState("")
  const [nickname, setNickname] = useState("")

  function copyToClipboard(text: string, key: string) {
    navigator.clipboard.writeText(text)
    setCopied(key)
    setTimeout(() => setCopied(null), 1500)
  }

  async function handleUpload(type: "purchase" | "review") {
    const fileInput = type === "purchase" ? purchaseRef.current : reviewRef.current
    if (!fileInput?.files?.length || !user || !task) return

    setUploading(true)
    setUploadMsg("")

    const formData = new FormData()
    formData.append("file", fileInput.files[0])
    formData.append("name", user.name)
    formData.append("phone", user.phone)

    if (type === "purchase") {
      formData.append("order_number", orderNumber)
      formData.append("recipient_name", recipientName || task.prev_info?.recipientName || "")
      formData.append("recipient_phone", phone || task.prev_info?.phone || "")
      formData.append("address", address || task.prev_info?.address || "")
      formData.append("bank", bank || task.prev_info?.bank || "")
      formData.append("account", account || task.prev_info?.account || "")
      formData.append("depositor", depositor || task.prev_info?.depositor || "")
      formData.append("nickname", nickname)
    }

    try {
      const endpoint = type === "purchase"
        ? `/api/task/${id}/purchase`
        : `/api/task/${id}/review`

      const result = await apiClient<{ ok: boolean; message: string }>(endpoint, {
        method: "POST",
        body: formData,
      })
      setUploadMsg(result.message || "제출 완료")
      mutate()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "제출 중 오류가 발생했습니다."
      setUploadMsg(message)
    } finally {
      setUploading(false)
    }
  }

  if (isLoading) {
    return (
      <div className="flex flex-col gap-4 p-4">
        <div className="h-8 w-24 animate-pulse rounded-md bg-muted" />
        <div className="h-48 animate-pulse rounded-xl bg-muted" />
        <div className="h-64 animate-pulse rounded-xl bg-muted" />
      </div>
    )
  }

  if (!task) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <p className="text-muted-foreground">작업을 찾을 수 없습니다.</p>
        <Button variant="outline" className="mt-4" onClick={() => router.back()}>돌아가기</Button>
      </div>
    )
  }

  const showPurchaseForm = task.status === "구매캡쳐대기" || task.status === "가이드전달"
  const showReviewForm = task.status === "리뷰대기"
  const camp = task.campaign

  return (
    <div className="flex flex-col">
      <header className="sticky top-0 z-40 flex items-center gap-3 border-b border-border bg-card px-4 py-3">
        <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0" onClick={() => router.back()} aria-label="뒤로가기">
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div className="flex-1 min-w-0">
          <p className="text-base font-semibold text-foreground truncate">{camp.product_name}</p>
          <p className="text-xs text-muted-foreground">{task.store_name} ({task.store_id})</p>
        </div>
        <TaskStatusBadge status={task.status} />
      </header>

      <div className="flex flex-col gap-4 p-4">
        {/* Campaign Guide */}
        <Card>
          <button
            className="flex w-full items-center justify-between p-4 text-left"
            onClick={() => setGuideOpen(!guideOpen)}
          >
            <span className="text-sm font-semibold text-foreground">캠페인 가이드</span>
            {guideOpen ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
          </button>
          {guideOpen && (
            <CardContent className="flex flex-col gap-3 p-4 pt-0 border-t border-border">
              {camp.product_link && (
                <a
                  href={camp.product_link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 text-sm font-medium text-primary-foreground bg-primary rounded-lg px-4 py-2.5 transition-opacity hover:opacity-90"
                >
                  <ExternalLink className="h-4 w-4" />
                  상품 링크
                </a>
              )}
              {camp.keyword && (
                <CopyRow label="키워드" value={camp.keyword} copied={copied} onCopy={copyToClipboard} />
              )}
              {camp.options && (
                <CopyRow label="옵션" value={camp.options} copied={copied} onCopy={copyToClipboard} />
              )}
              {camp.campaign_guide && (
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1">캠페인 가이드</p>
                  <p className="whitespace-pre-wrap text-sm text-foreground leading-relaxed">{camp.campaign_guide}</p>
                </div>
              )}
              {camp.review_guide && (
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1">리뷰 가이드</p>
                  <p className="whitespace-pre-wrap text-sm text-foreground leading-relaxed">{camp.review_guide}</p>
                </div>
              )}
              <div className="grid grid-cols-2 gap-2 text-xs">
                {camp.dwell_time && <span className="text-muted-foreground">체류시간: {camp.dwell_time}</span>}
                {camp.bookmark_required && <span className="text-muted-foreground">찜: {camp.bookmark_required}</span>}
                {camp.entry_method && <span className="text-muted-foreground">진입: {camp.entry_method}</span>}
                {camp.ship_memo_required && <span className="text-muted-foreground">배송메모: {camp.ship_memo_content || "필요"}</span>}
              </div>
            </CardContent>
          )}
        </Card>

        {/* Purchase submission */}
        {showPurchaseForm && (
          <Card className="border-primary/30">
            <CardHeader className="p-4 pb-2">
              <CardTitle className="flex items-center gap-2 text-sm">
                <Camera className="h-4 w-4" />
                구매 캡쳐 제출
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3 p-4 pt-2">
              <div className="grid grid-cols-2 gap-3">
                <FormField label="주문번호" value={orderNumber} onChange={setOrderNumber} />
                <FormField label="수령인" value={recipientName} onChange={setRecipientName} placeholder={task.prev_info?.recipientName} />
                <FormField label="연락처" value={phone} onChange={setPhone} placeholder={task.prev_info?.phone} />
                <FormField label="닉네임" value={nickname} onChange={setNickname} />
              </div>
              <FormField label="주소" value={address} onChange={setAddress} placeholder={task.prev_info?.address} />
              <div className="grid grid-cols-3 gap-3">
                <FormField label="은행" value={bank} onChange={setBank} placeholder={task.prev_info?.bank} />
                <FormField label="계좌번호" value={account} onChange={setAccount} placeholder={task.prev_info?.account} />
                <FormField label="예금주" value={depositor} onChange={setDepositor} placeholder={task.prev_info?.depositor} />
              </div>

              {task.bank_presets && task.bank_presets.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  <span className="text-xs text-muted-foreground w-full">이전 계좌:</span>
                  {task.bank_presets.map((p, i) => (
                    <button
                      key={i}
                      onClick={() => { setBank(p.bank); setAccount(p.account); setDepositor(p.depositor) }}
                      className="rounded-md bg-muted px-2 py-1 text-xs text-foreground hover:bg-accent transition-colors"
                    >
                      {p.bank} {p.account} ({p.depositor})
                    </button>
                  ))}
                </div>
              )}

              <div>
                <Label htmlFor="purchase-file" className="text-xs">구매 캡쳐 이미지</Label>
                <input
                  ref={purchaseRef}
                  id="purchase-file"
                  type="file"
                  accept="image/*"
                  capture="environment"
                  className="mt-1 block w-full text-sm file:mr-2 file:rounded-md file:border-0 file:bg-primary file:px-3 file:py-1.5 file:text-sm file:text-primary-foreground file:cursor-pointer"
                />
              </div>

              <Button onClick={() => handleUpload("purchase")} disabled={uploading} className="w-full">
                <Upload className="mr-2 h-4 w-4" />
                {uploading ? "제출 중..." : "구매 캡쳐 제출"}
              </Button>
            </CardContent>
          </Card>
        )}

        {/* Review submission */}
        {showReviewForm && (
          <Card className="border-primary/30">
            <CardHeader className="p-4 pb-2">
              <CardTitle className="flex items-center gap-2 text-sm">
                <ImageIcon className="h-4 w-4" />
                리뷰 캡쳐 제출
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3 p-4 pt-2">
              <div>
                <Label htmlFor="review-file" className="text-xs">리뷰 캡쳐 이미지</Label>
                <input
                  ref={reviewRef}
                  id="review-file"
                  type="file"
                  accept="image/*"
                  capture="environment"
                  className="mt-1 block w-full text-sm file:mr-2 file:rounded-md file:border-0 file:bg-primary file:px-3 file:py-1.5 file:text-sm file:text-primary-foreground file:cursor-pointer"
                />
              </div>
              <Button onClick={() => handleUpload("review")} disabled={uploading} className="w-full">
                <Upload className="mr-2 h-4 w-4" />
                {uploading ? "제출 중..." : "리뷰 캡쳐 제출"}
              </Button>
            </CardContent>
          </Card>
        )}

        {/* Upload result message */}
        {uploadMsg && (
          <div className={cn(
            "rounded-lg p-3 text-sm",
            uploadMsg.includes("오류") || uploadMsg.includes("실패")
              ? "bg-destructive/10 text-destructive"
              : "bg-success/10 text-success"
          )}>
            {uploadMsg}
          </div>
        )}

        {/* Existing captures */}
        {(task.purchase_capture || task.review_capture) && (
          <Card>
            <CardHeader className="p-4 pb-2">
              <CardTitle className="text-sm">제출된 캡쳐</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3 p-4 pt-0">
              {task.purchase_capture && (
                <div>
                  <p className="text-xs text-muted-foreground mb-1">구매 캡쳐</p>
                  <img
                    src={task.purchase_capture}
                    alt="구매 캡쳐"
                    className="rounded-lg border border-border max-h-48 object-contain"
                    crossOrigin="anonymous"
                  />
                </div>
              )}
              {task.review_capture && (
                <div>
                  <p className="text-xs text-muted-foreground mb-1">리뷰 캡쳐</p>
                  <img
                    src={task.review_capture}
                    alt="리뷰 캡쳐"
                    className="rounded-lg border border-border max-h-48 object-contain"
                    crossOrigin="anonymous"
                  />
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {/* Siblings */}
        {task.siblings && task.siblings.length > 1 && (
          <Card>
            <CardHeader className="p-4 pb-2">
              <CardTitle className="text-sm">같은 캠페인 다른 건</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2 p-4 pt-0">
              {task.siblings.filter(s => s.id !== task.id).map((s) => (
                <button
                  key={s.id}
                  onClick={() => router.push(`/task/${s.id}`)}
                  className="flex items-center justify-between rounded-lg bg-muted p-2.5 text-left hover:bg-accent transition-colors"
                >
                  <span className="text-sm text-foreground">#{s.id} - {s.store_id}</span>
                  <TaskStatusBadge status={s.status} />
                </button>
              ))}
            </CardContent>
          </Card>
        )}

        {/* Additional info */}
        {task.remark && (
          <Card>
            <CardContent className="p-4">
              <p className="text-xs text-muted-foreground mb-1">관리자 메모</p>
              <p className="text-sm text-foreground">{task.remark}</p>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}

function FormField({
  label, value, onChange, placeholder,
}: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string
}) {
  return (
    <div>
      <Label className="text-xs">{label}</Label>
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder || ""}
        className="mt-1 h-9 text-sm"
      />
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

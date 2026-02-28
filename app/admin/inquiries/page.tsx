"use client"

import { useState } from "react"
import useSWR from "swr"
import { adminFetcher, adminApi } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"

interface Inquiry {
  id: number
  reviewer_name: string
  reviewer_phone: string
  message: string
  is_urgent: boolean
  status: string
  reply: string
  created_at: string
  replied_at: string
}

export default function InquiriesPage() {
  const [statusFilter, setStatusFilter] = useState("")
  const { data, mutate } = useSWR<{ inquiries: Inquiry[] }>(
    `/admin/api/inquiries?status=${statusFilter}`,
    adminFetcher
  )
  const [replyText, setReplyText] = useState<Record<number, string>>({})
  const [replying, setReplying] = useState<number | null>(null)

  const items = data?.inquiries ?? []
  const statuses = ["", "대기", "답변완료"]

  const handleReply = async (inquiryId: number) => {
    const text = replyText[inquiryId]?.trim()
    if (!text) return
    setReplying(inquiryId)
    try {
      await adminApi("/admin/api/inquiry/reply", {
        method: "POST",
        body: JSON.stringify({ inquiry_id: inquiryId, reply: text }),
      })
      setReplyText((prev) => ({ ...prev, [inquiryId]: "" }))
      mutate()
    } finally {
      setReplying(null)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">문의 관리</h1>
        <p className="text-sm text-muted-foreground mt-1">리뷰어 문의를 확인하고 답변합니다</p>
      </div>

      <div className="flex gap-1">
        {statuses.map((s) => (
          <button
            key={s || "all"}
            onClick={() => setStatusFilter(s)}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
              statusFilter === s
                ? "bg-foreground text-background"
                : "bg-muted text-muted-foreground hover:bg-muted/80"
            }`}
          >
            {s || "전체"}
          </button>
        ))}
      </div>

      {items.length === 0 ? (
        <Card>
          <CardContent className="py-12">
            <p className="text-center text-muted-foreground">문의가 없습니다</p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {items.map((inquiry) => (
            <Card key={inquiry.id} className={inquiry.is_urgent ? "border-destructive/50" : ""}>
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <CardTitle className="text-base">{inquiry.reviewer_name}</CardTitle>
                    <span className="text-xs text-muted-foreground">{inquiry.reviewer_phone}</span>
                    {inquiry.is_urgent && (
                      <Badge variant="destructive" className="text-xs">긴급</Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant={inquiry.status === "답변완료" ? "default" : "secondary"}>
                      {inquiry.status}
                    </Badge>
                    <span className="text-xs text-muted-foreground">{inquiry.created_at}</span>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                {/* 문의 내용 */}
                <div className="bg-muted/50 rounded-lg p-4">
                  <p className="text-sm text-foreground whitespace-pre-wrap">{inquiry.message}</p>
                </div>

                {/* 기존 답변 */}
                {inquiry.reply && (
                  <div className="bg-primary/5 border border-primary/20 rounded-lg p-4">
                    <p className="text-xs font-medium text-muted-foreground mb-1">답변 ({inquiry.replied_at})</p>
                    <p className="text-sm text-foreground whitespace-pre-wrap">{inquiry.reply}</p>
                  </div>
                )}

                {/* 답변 입력 */}
                {inquiry.status !== "답변완료" && (
                  <div className="flex gap-2">
                    <textarea
                      value={replyText[inquiry.id] || ""}
                      onChange={(e) => setReplyText((prev) => ({ ...prev, [inquiry.id]: e.target.value }))}
                      placeholder="답변을 입력하세요..."
                      className="flex-1 min-h-20 rounded-lg border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring resize-none"
                    />
                    <Button
                      size="sm"
                      onClick={() => handleReply(inquiry.id)}
                      disabled={!replyText[inquiry.id]?.trim() || replying === inquiry.id}
                      className="self-end"
                    >
                      {replying === inquiry.id ? "전송 중..." : "답변"}
                    </Button>
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}

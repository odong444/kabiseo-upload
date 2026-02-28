"use client"

import { useState, useCallback } from "react"
import useSWR from "swr"
import { adminFetcher, adminApi } from "@/lib/api"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"

interface ProgressRow {
  id: number
  진행자이름: string
  진행자연락처: string
  수취인명: string
  연락처: string
  아이디: string
  은행: string
  계좌: string
  예금주: string
  제품명: string
  캠페인ID: string
  상태: string
  스토어ID: string
  구매일: string
  리뷰제출일: string
  입금금액: string
  입금일: string
}

interface Campaign {
  캠페인ID: string
  캠페인명?: string
  상품명?: string
}

const COLUMNS: { key: keyof ProgressRow; label: string; editable?: boolean }[] = [
  { key: "진행자이름", label: "이름" },
  { key: "진행자연락처", label: "연락처" },
  { key: "수취인명", label: "수취인", editable: true },
  { key: "아이디", label: "아이디", editable: true },
  { key: "은행", label: "은행", editable: true },
  { key: "계좌", label: "계좌", editable: true },
  { key: "예금주", label: "예금주", editable: true },
  { key: "제품명", label: "제품" },
  { key: "상태", label: "상태", editable: true },
  { key: "스토어ID", label: "스토어ID" },
  { key: "구매일", label: "구매일" },
  { key: "리뷰제출일", label: "리뷰일" },
  { key: "입금금액", label: "입금액", editable: true },
  { key: "입금일", label: "입금일" },
]

export default function SpreadsheetPage() {
  const [campaignFilter, setCampaignFilter] = useState("")
  const [statusFilter, setStatusFilter] = useState("")
  const { data, mutate } = useSWR<{ items: ProgressRow[]; campaigns: Campaign[] }>(
    `/admin/api/spreadsheet?campaign=${campaignFilter}&status=${statusFilter}`,
    adminFetcher
  )

  const items = data?.items ?? []
  const campaigns = data?.campaigns ?? []

  const [editing, setEditing] = useState<{ id: number; field: string } | null>(null)
  const [editValue, setEditValue] = useState("")

  const startEdit = (id: number, field: string, currentValue: string) => {
    setEditing({ id, field })
    setEditValue(currentValue)
  }

  const saveEdit = useCallback(async () => {
    if (!editing) return
    await adminApi("/admin/api/progress/update", {
      method: "POST",
      body: JSON.stringify({
        progress_id: editing.id,
        field: editing.field,
        value: editValue,
      }),
    })
    setEditing(null)
    mutate()
  }, [editing, editValue, mutate])

  const deleteRow = useCallback(async (id: number) => {
    if (!confirm("이 행을 삭제하시겠습니까?")) return
    await adminApi("/admin/api/progress/delete", {
      method: "POST",
      body: JSON.stringify({ progress_id: id }),
    })
    mutate()
  }, [mutate])

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">스프레드시트</h1>
        <p className="text-sm text-muted-foreground mt-1">전체 진행 데이터를 편집합니다. 셀을 클릭하면 수정할 수 있습니다.</p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <select
          value={campaignFilter}
          onChange={(e) => setCampaignFilter(e.target.value)}
          className="h-9 rounded-lg border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <option value="">전체 캠페인</option>
          {campaigns.map((c) => (
            <option key={c.캠페인ID} value={c.캠페인ID}>
              {c.캠페인명 || c.상품명 || c.캠페인ID}
            </option>
          ))}
        </select>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="h-9 rounded-lg border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <option value="">전체 상태</option>
          <option value="가이드전달">가이드전달</option>
          <option value="구매완료">구매완료</option>
          <option value="리뷰완료">리뷰완료</option>
          <option value="입금대기">입금대기</option>
          <option value="입금완료">입금완료</option>
          <option value="타임아웃">타임아웃</option>
        </select>
        <Badge variant="secondary">{items.length}건</Badge>
      </div>

      <Card>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border bg-muted/50">
                  {COLUMNS.map((col) => (
                    <th key={col.key} className="px-3 py-2.5 text-left font-medium text-muted-foreground whitespace-nowrap">
                      {col.label}
                    </th>
                  ))}
                  <th className="px-3 py-2.5 w-10" />
                </tr>
              </thead>
              <tbody>
                {items.length === 0 ? (
                  <tr>
                    <td colSpan={COLUMNS.length + 1} className="text-center py-12 text-muted-foreground">
                      데이터가 없습니다
                    </td>
                  </tr>
                ) : (
                  items.map((row) => (
                    <tr key={row.id} className="border-b border-border/50 hover:bg-muted/20">
                      {COLUMNS.map((col) => {
                        const isEditing = editing?.id === row.id && editing?.field === col.key
                        const cellValue = String(row[col.key] ?? "")

                        return (
                          <td key={col.key} className="px-3 py-2 whitespace-nowrap max-w-32">
                            {isEditing ? (
                              <Input
                                value={editValue}
                                onChange={(e) => setEditValue(e.target.value)}
                                onBlur={saveEdit}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter") saveEdit()
                                  if (e.key === "Escape") setEditing(null)
                                }}
                                autoFocus
                                className="h-7 text-xs px-1"
                              />
                            ) : (
                              <span
                                className={`truncate block ${col.editable ? "cursor-pointer hover:bg-primary/5 rounded px-1 -mx-1" : ""}`}
                                onClick={() => col.editable && startEdit(row.id, col.key, cellValue)}
                                title={cellValue}
                              >
                                {cellValue || "-"}
                              </span>
                            )}
                          </td>
                        )
                      })}
                      <td className="px-3 py-2">
                        <button
                          onClick={() => deleteRow(row.id)}
                          className="text-destructive/60 hover:text-destructive text-xs"
                          title="삭제"
                        >
                          X
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

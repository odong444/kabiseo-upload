"use client"

import { useState, useCallback } from "react"
import useSWR from "swr"
import { adminFetcher, adminApi } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"

interface ReviewerRow {
  id: number
  진행자이름: string
  진행자연락처: string
  수취인명: string
  연락처: string
  아이디: string
  제품명: string
  캠페인ID: string
  상태: string
  구매일: string
  리뷰제출일: string
  입금일: string
}

const STATUS_COLORS: Record<string, string> = {
  가이드전달: "bg-blue-100 text-blue-800",
  구매완료: "bg-amber-100 text-amber-800",
  리뷰완료: "bg-green-100 text-green-800",
  입금대기: "bg-purple-100 text-purple-800",
  입금완료: "bg-emerald-100 text-emerald-800",
  타임아웃: "bg-red-100 text-red-800",
  취소: "bg-gray-100 text-gray-800",
}

export default function ReviewersPage() {
  const [search, setSearch] = useState("")
  const [statusFilter, setStatusFilter] = useState("")
  const { data, mutate } = useSWR<{ items: ReviewerRow[] }>(
    `/admin/api/reviewers?q=${encodeURIComponent(search)}&status=${statusFilter}`,
    adminFetcher
  )
  const [selected, setSelected] = useState<Set<number>>(new Set())

  const items = data?.items ?? []

  const toggleSelect = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleRestore = useCallback(async () => {
    if (selected.size === 0) return
    await adminApi("/admin/reviewers/restore", {
      method: "POST",
      body: JSON.stringify({ row_ids: Array.from(selected) }),
    })
    setSelected(new Set())
    mutate()
  }, [selected, mutate])

  const statuses = ["", "가이드전달", "구매완료", "리뷰완료", "입금대기", "입금완료", "타임아웃", "취소"]

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">리뷰어 관리</h1>
        <p className="text-sm text-muted-foreground mt-1">전체 진행 데이터를 조회하고 관리합니다</p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Input
          placeholder="이름, 연락처, 아이디 검색..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs"
        />
        <div className="flex gap-1 flex-wrap">
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
        {selected.size > 0 && (
          <Button size="sm" variant="outline" onClick={handleRestore}>
            {selected.size}건 가이드전달 복원
          </Button>
        )}
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">
            진행 목록
            <Badge variant="secondary" className="ml-2">{items.length}건</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {items.length === 0 ? (
            <p className="text-center py-12 text-muted-foreground">데이터가 없습니다</p>
          ) : (
            <div className="overflow-x-auto -mx-6">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/50">
                    <th className="px-4 py-3 w-10">
                      <input
                        type="checkbox"
                        onChange={() => {
                          if (selected.size === items.length) setSelected(new Set())
                          else setSelected(new Set(items.map((i) => i.id)))
                        }}
                        checked={selected.size === items.length && items.length > 0}
                        className="rounded border-border"
                      />
                    </th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">이름</th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">연락처</th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">아이디</th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">제품</th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">상태</th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">구매일</th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">리뷰제출일</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr
                      key={item.id}
                      className={`border-b border-border/50 hover:bg-muted/30 transition-colors ${
                        selected.has(item.id) ? "bg-primary/5" : ""
                      }`}
                    >
                      <td className="px-4 py-3">
                        <input
                          type="checkbox"
                          checked={selected.has(item.id)}
                          onChange={() => toggleSelect(item.id)}
                          className="rounded border-border"
                        />
                      </td>
                      <td className="px-4 py-3 font-medium text-foreground">{item.진행자이름}</td>
                      <td className="px-4 py-3 text-muted-foreground">{item.진행자연락처}</td>
                      <td className="px-4 py-3 text-foreground">{item.아이디}</td>
                      <td className="px-4 py-3 text-foreground max-w-40 truncate">{item.제품명}</td>
                      <td className="px-4 py-3">
                        <span className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${STATUS_COLORS[item.상태] || "bg-muted text-muted-foreground"}`}>
                          {item.상태}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-muted-foreground text-xs">{item.구매일}</td>
                      <td className="px-4 py-3 text-muted-foreground text-xs">{item.리뷰제출일}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

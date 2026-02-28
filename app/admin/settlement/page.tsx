"use client"

import { useState, useCallback } from "react"
import useSWR from "swr"
import { adminFetcher, adminApi } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"

interface SettlementItem {
  id: number
  진행자이름: string
  진행자연락처: string
  수취인명: string
  연락처: string
  은행: string
  계좌: string
  예금주: string
  아이디: string
  제품명: string
  입금금액: string
  리뷰제출일: string
  상태: string
}

export default function SettlementPage() {
  const { data, mutate } = useSWR<{ items: SettlementItem[] }>("/admin/api/settlement", adminFetcher)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [processing, setProcessing] = useState(false)

  const items = data?.items ?? []

  const toggleSelect = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleAll = () => {
    if (selected.size === items.length) {
      setSelected(new Set())
    } else {
      setSelected(new Set(items.map((i) => i.id)))
    }
  }

  const processSettlement = useCallback(async () => {
    if (selected.size === 0) return
    setProcessing(true)
    try {
      await adminApi("/admin/settlement/process", {
        method: "POST",
        body: JSON.stringify({ row_ids: Array.from(selected) }),
      })
      setSelected(new Set())
      mutate()
    } finally {
      setProcessing(false)
    }
  }, [selected, mutate])

  const downloadCsv = () => {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || ""
    window.open(`${apiUrl}/admin/settlement/download`, "_blank")
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">정산 관리</h1>
          <p className="text-sm text-muted-foreground mt-1">
            입금대기 상태의 리뷰어에게 리뷰비를 정산합니다
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={downloadCsv}>
            CSV 다운로드
          </Button>
          <Button
            size="sm"
            onClick={processSettlement}
            disabled={selected.size === 0 || processing}
          >
            {processing ? "처리 중..." : `${selected.size}건 정산 처리`}
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-base">
              입금대기 목록
              <Badge variant="secondary" className="ml-2">
                {items.length}건
              </Badge>
            </CardTitle>
            <Button variant="ghost" size="sm" onClick={toggleAll}>
              {selected.size === items.length && items.length > 0 ? "전체 해제" : "전체 선택"}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {items.length === 0 ? (
            <p className="text-center py-12 text-muted-foreground">입금대기 건이 없습니다</p>
          ) : (
            <div className="overflow-x-auto -mx-6">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/50">
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground w-10">
                      <input
                        type="checkbox"
                        checked={selected.size === items.length && items.length > 0}
                        onChange={toggleAll}
                        className="rounded border-border"
                      />
                    </th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">수취인</th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">은행/계좌</th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">아이디</th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">제품명</th>
                    <th className="px-4 py-3 text-right font-medium text-muted-foreground">입금금액</th>
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">리뷰제출일</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr
                      key={item.id}
                      className={`border-b border-border/50 hover:bg-muted/30 transition-colors cursor-pointer ${
                        selected.has(item.id) ? "bg-primary/5" : ""
                      }`}
                      onClick={() => toggleSelect(item.id)}
                    >
                      <td className="px-4 py-3">
                        <input
                          type="checkbox"
                          checked={selected.has(item.id)}
                          onChange={() => toggleSelect(item.id)}
                          onClick={(e) => e.stopPropagation()}
                          className="rounded border-border"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <div className="font-medium text-foreground">{item.수취인명 || item.진행자이름}</div>
                        <div className="text-xs text-muted-foreground">{item.연락처 || item.진행자연락처}</div>
                      </td>
                      <td className="px-4 py-3">
                        <div className="text-foreground">{item.은행}</div>
                        <div className="text-xs text-muted-foreground">{item.계좌}</div>
                        {item.예금주 && (
                          <div className="text-xs text-muted-foreground">{item.예금주}</div>
                        )}
                      </td>
                      <td className="px-4 py-3 text-foreground">{item.아이디}</td>
                      <td className="px-4 py-3 text-foreground max-w-48 truncate">{item.제품명}</td>
                      <td className="px-4 py-3 text-right font-semibold text-foreground">
                        {Number(item.입금금액 || 0).toLocaleString()}원
                      </td>
                      <td className="px-4 py-3 text-muted-foreground">{item.리뷰제출일}</td>
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

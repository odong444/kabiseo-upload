"use client"

import { useState } from "react"
import useSWR from "swr"
import { adminFetcher } from "@/lib/api"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"

interface LogEntry {
  id: number
  type: string
  message: string
  details: string
  created_at: string
}

const TYPE_COLORS: Record<string, string> = {
  info: "bg-blue-100 text-blue-800",
  warning: "bg-amber-100 text-amber-800",
  error: "bg-red-100 text-red-800",
  purchase: "bg-emerald-100 text-emerald-800",
  review: "bg-purple-100 text-purple-800",
  settlement: "bg-teal-100 text-teal-800",
  kakao: "bg-yellow-100 text-yellow-800",
  timeout: "bg-orange-100 text-orange-800",
}

export default function LogsPage() {
  const [typeFilter, setTypeFilter] = useState("")
  const { data } = useSWR<{ logs: LogEntry[] }>(
    `/admin/api/logs?type=${typeFilter}`,
    adminFetcher
  )

  const logs = data?.logs ?? []
  const types = ["", "info", "warning", "error", "purchase", "review", "settlement", "kakao", "timeout"]

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">활동 로그</h1>
        <p className="text-sm text-muted-foreground mt-1">시스템 활동 로그를 조회합니다</p>
      </div>

      <div className="flex gap-1 flex-wrap">
        {types.map((t) => (
          <button
            key={t || "all"}
            onClick={() => setTypeFilter(t)}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
              typeFilter === t
                ? "bg-foreground text-background"
                : "bg-muted text-muted-foreground hover:bg-muted/80"
            }`}
          >
            {t || "전체"}
          </button>
        ))}
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">
            로그
            <Badge variant="secondary" className="ml-2">{logs.length}건</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {logs.length === 0 ? (
            <p className="text-center py-12 text-muted-foreground">로그가 없습니다</p>
          ) : (
            <div className="space-y-2 max-h-[700px] overflow-y-auto">
              {logs.map((log) => (
                <div
                  key={log.id}
                  className="flex items-start gap-3 px-4 py-3 rounded-lg bg-muted/30 hover:bg-muted/50 transition-colors"
                >
                  <span className={`inline-flex px-2 py-0.5 rounded text-xs font-medium shrink-0 mt-0.5 ${TYPE_COLORS[log.type] || "bg-muted text-muted-foreground"}`}>
                    {log.type}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-foreground">{log.message}</p>
                    {log.details && (
                      <p className="text-xs text-muted-foreground mt-1 truncate">{log.details}</p>
                    )}
                  </div>
                  <span className="text-xs text-muted-foreground/60 shrink-0">{log.created_at}</span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

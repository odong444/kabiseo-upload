"use client"

import { useState } from "react"
import useSWR from "swr"
import Link from "next/link"
import { useAuth } from "@/lib/auth"
import { apiClient } from "@/lib/api"
import { Card, CardContent } from "@/components/ui/card"
import { TaskStatusBadge } from "@/components/task-status-badge"
import { RefreshCw, ChevronRight, Inbox } from "lucide-react"
import { Button } from "@/components/ui/button"
import type { TaskItem } from "@/lib/types"

interface TaskListResponse {
  in_progress: TaskItem[]
  completed: TaskItem[]
}

const TABS = [
  { key: "progress", label: "진행 중" },
  { key: "completed", label: "완료" },
] as const

export default function MyTasksPage() {
  const { user } = useAuth()
  const [tab, setTab] = useState<"progress" | "completed">("progress")

  const { data, isLoading, mutate } = useSWR<TaskListResponse>(
    user ? `/api/my?name=${encodeURIComponent(user.name)}&phone=${user.phone}` : null,
    (url: string) => apiClient<TaskListResponse>(url)
  )

  const tasks = tab === "progress" ? data?.in_progress : data?.completed

  return (
    <div className="flex flex-col">
      <header className="sticky top-0 z-40 border-b border-border bg-card px-4 py-3">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-bold text-foreground">내 작업</h1>
          <Button variant="ghost" size="icon" onClick={() => mutate()} aria-label="새로고침" className="h-8 w-8">
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
        <div className="mt-3 flex rounded-lg bg-muted p-1">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                tab === t.key
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground"
              }`}
            >
              {t.label}
              {data && (
                <span className="ml-1 text-xs">
                  ({t.key === "progress" ? data.in_progress.length : data.completed.length})
                </span>
              )}
            </button>
          ))}
        </div>
      </header>

      <div className="flex flex-col gap-3 p-4">
        {isLoading ? (
          Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-20 animate-pulse rounded-xl bg-muted" />
          ))
        ) : tasks && tasks.length > 0 ? (
          tasks.map((task) => (
            <Link key={task.id} href={`/task/${task.id}`}>
              <Card className="transition-shadow hover:shadow-md">
                <CardContent className="flex items-center gap-3 p-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <TaskStatusBadge status={task.status} />
                      <span className="text-xs text-muted-foreground">{task.date}</span>
                    </div>
                    <p className="text-sm font-medium text-foreground truncate">{task.product_name}</p>
                    <p className="text-xs text-muted-foreground truncate">{task.store_name} ({task.store_id})</p>
                  </div>
                  <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                </CardContent>
              </Card>
            </Link>
          ))
        ) : (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <Inbox className="mb-3 h-10 w-10 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              {tab === "progress" ? "진행 중인 작업이 없습니다." : "완료된 작업이 없습니다."}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

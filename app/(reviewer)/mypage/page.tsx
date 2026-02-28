"use client"

import { useState } from "react"
import useSWR from "swr"
import Link from "next/link"
import { useAuth } from "@/lib/auth"
import { apiClient } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { TaskStatusBadge } from "@/components/task-status-badge"
import {
  LogOut, Wallet, RefreshCw, Clock, CheckCircle, AlertCircle,
  Activity, ChevronRight,
} from "lucide-react"
import type { PaymentResponse, TaskItem } from "@/lib/types"
import { useRouter } from "next/navigation"

interface StatusResponse {
  in_progress: TaskItem[]
  completed: TaskItem[]
}

const TABS = [
  { key: "status", label: "진행현황", icon: Activity },
  { key: "payment", label: "입금현황", icon: Wallet },
] as const

export default function MyPage() {
  const { user, logout } = useAuth()
  const router = useRouter()
  const [tab, setTab] = useState<"status" | "payment">("status")

  const { data: payment, isLoading: payLoading, mutate: mutatePay } = useSWR<PaymentResponse>(
    user ? `/api/payment?name=${encodeURIComponent(user.name)}&phone=${user.phone}` : null,
    (url: string) => apiClient<PaymentResponse>(url)
  )

  const { data: status, isLoading: statusLoading, mutate: mutateStatus } = useSWR<StatusResponse>(
    user ? `/api/status?name=${encodeURIComponent(user.name)}&phone=${user.phone}` : null,
    (url: string) => apiClient<StatusResponse>(url)
  )

  function handleLogout() {
    logout()
    router.replace("/login")
  }

  function refresh() {
    mutatePay()
    mutateStatus()
  }

  return (
    <div className="flex flex-col">
      <header className="sticky top-0 z-40 border-b border-border bg-card px-4 py-3">
        <div className="flex items-center justify-between mb-3">
          <h1 className="text-lg font-bold text-foreground">마이페이지</h1>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="icon" onClick={refresh} aria-label="새로고침" className="h-8 w-8">
              <RefreshCw className="h-4 w-4" />
            </Button>
          </div>
        </div>
        <div className="flex rounded-lg bg-muted p-1">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                tab === t.key
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground"
              }`}
            >
              <t.icon className="h-3.5 w-3.5" />
              {t.label}
            </button>
          ))}
        </div>
      </header>

      <div className="flex flex-col gap-4 p-4">
        {/* User Info */}
        <Card>
          <CardContent className="flex items-center justify-between p-4">
            <div>
              <p className="font-semibold text-foreground">{user?.name}</p>
              <p className="text-sm text-muted-foreground">{user?.phone}</p>
            </div>
            <Button variant="outline" size="sm" onClick={handleLogout}>
              <LogOut className="mr-1.5 h-3.5 w-3.5" />
              로그아웃
            </Button>
          </CardContent>
        </Card>

        {tab === "status" ? (
          <StatusTab status={status} isLoading={statusLoading} />
        ) : (
          <PaymentTab payment={payment} isLoading={payLoading} />
        )}
      </div>
    </div>
  )
}

/* ──── 진행현황 탭 ──── */
function StatusTab({ status, isLoading }: { status?: StatusResponse; isLoading: boolean }) {
  if (isLoading) {
    return (
      <div className="flex flex-col gap-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-20 animate-pulse rounded-xl bg-muted" />
        ))}
      </div>
    )
  }

  const inProgress = status?.in_progress || []
  const completed = status?.completed || []

  if (inProgress.length === 0 && completed.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <Activity className="mb-3 h-10 w-10 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">진행 중인 체험단이 없습니다.</p>
        <Link href="/campaigns" className="mt-3">
          <Button variant="outline" size="sm">캠페인 보기</Button>
        </Link>
      </div>
    )
  }

  return (
    <>
      {inProgress.length > 0 && (
        <Card>
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-sm">진행 중 ({inProgress.length}건)</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2 p-4 pt-0">
            {inProgress.map((item) => (
              <Link key={item.id} href={`/task/${item.id}`}>
                <div className="flex items-center justify-between rounded-lg bg-muted p-3 transition-colors hover:bg-accent">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                      <TaskStatusBadge status={item.status} />
                      {item.remark && item.remark.startsWith("반려") && (
                        <Badge variant="destructive" className="text-[10px]">반려</Badge>
                      )}
                    </div>
                    <p className="text-sm font-medium text-foreground truncate">{item.product_name}</p>
                    <p className="text-xs text-muted-foreground">{item.store_id} / {item.date}</p>
                  </div>
                  <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                </div>
              </Link>
            ))}
          </CardContent>
        </Card>
      )}

      {completed.length > 0 && (
        <Card>
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-sm">완료 ({completed.length}건)</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2 p-4 pt-0">
            {completed.map((item) => (
              <Link key={item.id} href={`/task/${item.id}`}>
                <div className="flex items-center justify-between rounded-lg bg-muted p-3 opacity-70 transition-colors hover:bg-accent">
                  <div className="flex-1 min-w-0">
                    <TaskStatusBadge status={item.status} />
                    <p className="text-sm font-medium text-foreground truncate mt-0.5">{item.product_name}</p>
                    <p className="text-xs text-muted-foreground">{item.store_id}</p>
                  </div>
                  <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                </div>
              </Link>
            ))}
          </CardContent>
        </Card>
      )}
    </>
  )
}

/* ──── 입금현황 탭 ──── */
function PaymentTab({ payment, isLoading }: { payment?: PaymentResponse; isLoading: boolean }) {
  if (isLoading) {
    return (
      <div className="flex flex-col gap-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-12 animate-pulse rounded-lg bg-muted" />
        ))}
      </div>
    )
  }

  const paid = payment?.paid || []
  const pending = payment?.pending || []
  const noReview = payment?.no_review || []

  return (
    <>
      {/* Summary cards */}
      <div className="grid grid-cols-3 gap-3">
        <SummaryCard
          icon={<CheckCircle className="h-4 w-4 text-success" />}
          label="입금완료"
          count={paid.length}
          amount={paid.reduce((s, p) => s + p.amount, 0)}
        />
        <SummaryCard
          icon={<Clock className="h-4 w-4 text-warning-foreground" />}
          label="입금대기"
          count={pending.length}
          amount={pending.reduce((s, p) => s + p.amount, 0)}
        />
        <SummaryCard
          icon={<AlertCircle className="h-4 w-4 text-destructive" />}
          label="리뷰미제출"
          count={noReview.length}
          amount={0}
        />
      </div>

      {pending.length > 0 && (
        <Card>
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-sm">입금 대기 내역</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2 p-4 pt-0">
            {pending.map((item, i) => (
              <div key={i} className="flex items-center justify-between rounded-lg bg-muted p-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-foreground truncate">{item.productName}</p>
                  <p className="text-xs text-muted-foreground">{item.storeId} / {item.date}</p>
                </div>
                <Badge variant="outline" className="shrink-0">{item.amount.toLocaleString()}원</Badge>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {noReview.length > 0 && (
        <Card>
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-sm">리뷰 미제출</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2 p-4 pt-0">
            {noReview.map((item, i) => (
              <div key={i} className="flex items-center justify-between rounded-lg bg-muted p-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-foreground truncate">{item.productName}</p>
                  <p className="text-xs text-muted-foreground">{item.storeId}</p>
                  {item.reviewDeadline && (
                    <p className="text-xs text-destructive">기한: {item.reviewDeadline}</p>
                  )}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {paid.length > 0 && (
        <Card>
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-sm">입금 완료 내역</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2 p-4 pt-0">
            {paid.map((item, i) => (
              <div key={i} className="flex items-center justify-between rounded-lg bg-muted p-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-foreground truncate">{item.productName}</p>
                  <p className="text-xs text-muted-foreground">{item.storeId} / {item.date}</p>
                </div>
                <Badge variant="default" className="shrink-0">{item.amount.toLocaleString()}원</Badge>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {paid.length === 0 && pending.length === 0 && noReview.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <Wallet className="mb-3 h-10 w-10 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">입금 내역이 없습니다.</p>
        </div>
      )}
    </>
  )
}

function SummaryCard({
  icon, label, count, amount,
}: {
  icon: React.ReactNode; label: string; count: number; amount: number
}) {
  return (
    <div className="flex flex-col items-center gap-1 rounded-xl border bg-card p-3 text-center shadow-sm">
      {icon}
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-lg font-bold text-foreground">{count}</span>
      {amount > 0 && <span className="text-xs text-muted-foreground">{amount.toLocaleString()}원</span>}
    </div>
  )
}

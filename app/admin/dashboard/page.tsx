"use client"

import useSWR from "swr"
import { apiClient } from "@/lib/api"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { RefreshCw, Users, Megaphone, ShoppingBag, Wallet, ClipboardCheck, Activity } from "lucide-react"
import { Button } from "@/components/ui/button"

interface DashboardStats {
  total_campaigns: number
  active_campaigns: number
  total_reviewers: number
  today_applications: number
  today_purchases: number
  today_reviews: number
  pending_reviews: number
  pending_settlements: number
}

interface RecentActivity {
  type: string
  message: string
  time: string
}

export default function AdminDashboard() {
  const { data: stats, isLoading, mutate } = useSWR<DashboardStats>(
    "/api/admin/dashboard",
    (url: string) => apiClient<DashboardStats>(url)
  )

  const { data: activities } = useSWR<RecentActivity[]>(
    "/api/admin/activities",
    (url: string) => apiClient<RecentActivity[]>(url)
  )

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">대시보드</h1>
          <p className="text-sm text-muted-foreground">오늘의 운영 현황을 한 눈에 확인하세요.</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => mutate()}>
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          새로고침
        </Button>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          icon={<Megaphone className="h-5 w-5" />}
          label="진행 캠페인"
          value={stats?.active_campaigns}
          sub={`전체 ${stats?.total_campaigns || 0}개`}
          isLoading={isLoading}
        />
        <StatCard
          icon={<Users className="h-5 w-5" />}
          label="전체 리뷰어"
          value={stats?.total_reviewers}
          isLoading={isLoading}
        />
        <StatCard
          icon={<ShoppingBag className="h-5 w-5" />}
          label="오늘 신청"
          value={stats?.today_applications}
          sub={`구매 ${stats?.today_purchases || 0} / 리뷰 ${stats?.today_reviews || 0}`}
          isLoading={isLoading}
        />
        <StatCard
          icon={<Wallet className="h-5 w-5" />}
          label="대기 건수"
          value={(stats?.pending_reviews || 0) + (stats?.pending_settlements || 0)}
          sub={`검수 ${stats?.pending_reviews || 0} / 정산 ${stats?.pending_settlements || 0}`}
          isLoading={isLoading}
          highlight
        />
      </div>

      {/* Quick Actions */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <QuickAction href="/admin/reviews" icon={<ClipboardCheck className="h-5 w-5" />} label="리뷰 검수" count={stats?.pending_reviews} />
        <QuickAction href="/admin/settlement" icon={<Wallet className="h-5 w-5" />} label="정산 관리" count={stats?.pending_settlements} />
        <QuickAction href="/admin/campaigns" icon={<Megaphone className="h-5 w-5" />} label="캠페인 관리" />
        <QuickAction href="/admin/reviewers" icon={<Users className="h-5 w-5" />} label="리뷰어 관리" />
      </div>

      {/* Recent Activities */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Activity className="h-4 w-4" />
            최근 활동
          </CardTitle>
        </CardHeader>
        <CardContent>
          {activities && activities.length > 0 ? (
            <div className="flex flex-col gap-2">
              {activities.slice(0, 20).map((a, i) => (
                <div key={i} className="flex items-center justify-between rounded-lg bg-muted px-3 py-2">
                  <div className="flex items-center gap-2">
                    <Badge variant="outline" className="text-[10px]">{a.type}</Badge>
                    <span className="text-sm text-foreground">{a.message}</span>
                  </div>
                  <span className="shrink-0 text-xs text-muted-foreground">{a.time}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground py-4 text-center">최근 활동이 없습니다.</p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function StatCard({
  icon, label, value, sub, isLoading, highlight,
}: {
  icon: React.ReactNode; label: string; value?: number; sub?: string; isLoading: boolean; highlight?: boolean
}) {
  return (
    <Card className={highlight && (value ?? 0) > 0 ? "border-primary/30 bg-primary/5" : ""}>
      <CardContent className="flex items-start gap-3 p-4">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          {icon}
        </div>
        <div>
          <p className="text-xs text-muted-foreground">{label}</p>
          {isLoading ? (
            <div className="mt-1 h-7 w-12 animate-pulse rounded bg-muted" />
          ) : (
            <>
              <p className="text-2xl font-bold text-foreground">{value ?? 0}</p>
              {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
            </>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function QuickAction({
  href, icon, label, count,
}: {
  href: string; icon: React.ReactNode; label: string; count?: number
}) {
  return (
    <a href={href} className="block">
      <Card className="transition-shadow hover:shadow-md">
        <CardContent className="flex items-center justify-between p-4">
          <div className="flex items-center gap-3">
            <span className="text-muted-foreground">{icon}</span>
            <span className="text-sm font-medium text-foreground">{label}</span>
          </div>
          {count !== undefined && count > 0 && (
            <Badge variant="destructive">{count}</Badge>
          )}
        </CardContent>
      </Card>
    </a>
  )
}

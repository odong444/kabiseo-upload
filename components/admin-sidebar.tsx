"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { useAdminAuth } from "@/lib/admin-auth"
import { cn } from "@/lib/utils"
import {
  LayoutDashboard, Megaphone, ClipboardCheck, Wallet, Users,
  MessageSquare, FileSpreadsheet, Activity, LogOut, ChevronLeft, ChevronRight,
} from "lucide-react"
import { useState } from "react"

const NAV_ITEMS = [
  { href: "/admin/dashboard", label: "대시보드", icon: LayoutDashboard },
  { href: "/admin/campaigns", label: "캠페인 관리", icon: Megaphone },
  { href: "/admin/reviews", label: "리뷰 검수", icon: ClipboardCheck },
  { href: "/admin/settlement", label: "정산", icon: Wallet },
  { href: "/admin/reviewers", label: "리뷰어 관리", icon: Users },
  { href: "/admin/chat-history", label: "대화 이력", icon: MessageSquare },
  { href: "/admin/spreadsheet", label: "스프레드시트", icon: FileSpreadsheet },
  { href: "/admin/logs", label: "로그", icon: Activity },
]

export function AdminSidebar() {
  const pathname = usePathname()
  const { logout } = useAdminAuth()
  const [collapsed, setCollapsed] = useState(false)

  return (
    <aside
      className={cn(
        "fixed inset-y-0 left-0 z-50 flex flex-col border-r border-border bg-card transition-all duration-200",
        collapsed ? "w-16" : "w-56"
      )}
    >
      <div className="flex h-14 items-center justify-between border-b border-border px-4">
        {!collapsed && (
          <Link href="/admin/dashboard" className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-sm font-bold text-primary-foreground">
              K
            </div>
            <span className="text-sm font-bold text-foreground">카비서 관리자</span>
          </Link>
        )}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="rounded-md p-1 text-muted-foreground hover:bg-accent transition-colors"
          aria-label={collapsed ? "사이드바 열기" : "사이드바 접기"}
        >
          {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
        </button>
      </div>

      <nav className="flex-1 overflow-y-auto py-2" aria-label="관리자 메뉴">
        {NAV_ITEMS.map((item) => {
          const isActive = pathname.startsWith(item.href)
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "mx-2 mb-0.5 flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                isActive
                  ? "bg-primary/10 text-foreground font-medium"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground"
              )}
              title={collapsed ? item.label : undefined}
            >
              <item.icon className="h-4 w-4 shrink-0" />
              {!collapsed && <span>{item.label}</span>}
            </Link>
          )
        })}
      </nav>

      <div className="border-t border-border p-2">
        <button
          onClick={logout}
          className="mx-0 flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          title={collapsed ? "로그아웃" : undefined}
        >
          <LogOut className="h-4 w-4 shrink-0" />
          {!collapsed && <span>로그아웃</span>}
        </button>
      </div>
    </aside>
  )
}

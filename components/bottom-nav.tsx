"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { LayoutGrid, ClipboardList, MessageCircle, User } from "lucide-react"
import { cn } from "@/lib/utils"

const NAV_ITEMS = [
  { href: "/campaigns", label: "캠페인", icon: LayoutGrid },
  { href: "/my", label: "내 작업", icon: ClipboardList },
  { href: "/chat", label: "채팅", icon: MessageCircle },
  { href: "/mypage", label: "마이", icon: User },
]

export function BottomNav() {
  const pathname = usePathname()

  return (
    <nav className="fixed bottom-0 left-0 right-0 z-50 border-t border-border bg-card" role="navigation" aria-label="메인 네비게이션">
      <div className="mx-auto flex max-w-lg items-center justify-around">
        {NAV_ITEMS.map((item) => {
          const isActive = pathname.startsWith(item.href)
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex flex-1 flex-col items-center gap-0.5 py-2.5 text-xs transition-colors",
                isActive
                  ? "text-primary-foreground font-semibold"
                  : "text-muted-foreground"
              )}
              aria-current={isActive ? "page" : undefined}
            >
              <span className={cn(
                "flex h-8 w-8 items-center justify-center rounded-lg transition-colors",
                isActive ? "bg-primary text-primary-foreground" : ""
              )}>
                <item.icon className="h-5 w-5" />
              </span>
              <span>{item.label}</span>
            </Link>
          )
        })}
      </div>
    </nav>
  )
}

"use client"

import { useAuth } from "@/lib/auth"
import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { BottomNav } from "@/components/bottom-nav"

export default function ReviewerLayout({ children }: { children: React.ReactNode }) {
  const { isLoggedIn } = useAuth()
  const router = useRouter()

  useEffect(() => {
    if (!isLoggedIn) {
      router.replace("/login")
    }
  }, [isLoggedIn, router])

  if (!isLoggedIn) return null

  return (
    <div className="mx-auto min-h-dvh max-w-lg bg-background">
      <main className="pb-20">{children}</main>
      <BottomNav />
    </div>
  )
}

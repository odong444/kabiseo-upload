"use client"

import { useEffect } from "react"
import { useRouter, usePathname } from "next/navigation"
import { AdminAuthProvider, useAdminAuth } from "@/lib/admin-auth"
import { AdminSidebar } from "@/components/admin-sidebar"

function AdminGuard({ children }: { children: React.ReactNode }) {
  const { isAdmin } = useAdminAuth()
  const router = useRouter()
  const pathname = usePathname()

  useEffect(() => {
    if (!isAdmin && pathname !== "/admin/login") {
      router.replace("/admin/login")
    }
  }, [isAdmin, pathname, router])

  if (!isAdmin && pathname !== "/admin/login") return null

  if (pathname === "/admin/login") {
    return <>{children}</>
  }

  return (
    <div className="flex min-h-dvh bg-background">
      <AdminSidebar />
      <main className="ml-56 flex-1 p-6">
        {children}
      </main>
    </div>
  )
}

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <AdminAuthProvider>
      <AdminGuard>{children}</AdminGuard>
    </AdminAuthProvider>
  )
}

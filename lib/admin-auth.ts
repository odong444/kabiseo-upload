"use client"

import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from "react"

interface AdminAuthContextType {
  isAdmin: boolean
  login: (password: string) => Promise<boolean>
  logout: () => void
}

const AdminAuthContext = createContext<AdminAuthContextType | null>(null)

const ADMIN_KEY = "kabiseo_admin"

export function AdminAuthProvider({ children }: { children: ReactNode }) {
  const [isAdmin, setIsAdmin] = useState(false)

  useEffect(() => {
    setIsAdmin(localStorage.getItem(ADMIN_KEY) === "true")
  }, [])

  const login = useCallback(async (password: string) => {
    // Check against environment variable or hardcoded for demo
    // In production, this should validate via the backend API
    try {
      const { apiClient } = await import("@/lib/api")
      const result = await apiClient<{ ok: boolean }>("/api/admin/login", {
        method: "POST",
        body: JSON.stringify({ password }),
      })
      if (result.ok) {
        localStorage.setItem(ADMIN_KEY, "true")
        setIsAdmin(true)
        return true
      }
    } catch {
      // fallback: reject
    }
    return false
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem(ADMIN_KEY)
    setIsAdmin(false)
  }, [])

  return (
    <AdminAuthContext value={{ isAdmin, login, logout }}>
      {children}
    </AdminAuthContext>
  )
}

export function useAdminAuth() {
  const context = useContext(AdminAuthContext)
  if (!context) throw new Error("useAdminAuth must be used within AdminAuthProvider")
  return context
}

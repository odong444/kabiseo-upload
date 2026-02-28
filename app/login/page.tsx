"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "@/lib/auth"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"

export default function LoginPage() {
  const [name, setName] = useState("")
  const [phone, setPhone] = useState("")
  const [error, setError] = useState("")
  const { login, isLoggedIn } = useAuth()
  const router = useRouter()

  if (isLoggedIn) {
    router.replace("/campaigns")
    return null
  }

  function formatPhone(value: string) {
    const digits = value.replace(/\D/g, "").slice(0, 11)
    if (digits.length <= 3) return digits
    if (digits.length <= 7) return `${digits.slice(0, 3)}-${digits.slice(3)}`
    return `${digits.slice(0, 3)}-${digits.slice(3, 7)}-${digits.slice(7)}`
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError("")

    const trimmedName = name.trim()
    const rawPhone = phone.replace(/\D/g, "")

    if (!trimmedName) {
      setError("이름을 입력해주세요.")
      return
    }
    if (rawPhone.length < 10) {
      setError("올바른 연락처를 입력해주세요.")
      return
    }

    login(trimmedName, rawPhone)
    router.push("/campaigns")
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-primary">
            <span className="text-2xl font-bold text-primary-foreground">K</span>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">카비서</h1>
          <p className="mt-1 text-sm text-muted-foreground">리뷰어 관리 플랫폼</p>
        </div>

        <Card>
          <CardHeader className="pb-4">
            <CardTitle className="text-lg">로그인</CardTitle>
            <CardDescription>이름과 연락처를 입력해주세요</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="flex flex-col gap-4">
              <div className="flex flex-col gap-2">
                <Label htmlFor="name">이름</Label>
                <Input
                  id="name"
                  type="text"
                  placeholder="홍길동"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoComplete="name"
                />
              </div>

              <div className="flex flex-col gap-2">
                <Label htmlFor="phone">연락처</Label>
                <Input
                  id="phone"
                  type="tel"
                  placeholder="010-1234-5678"
                  value={phone}
                  onChange={(e) => setPhone(formatPhone(e.target.value))}
                  autoComplete="tel"
                />
              </div>

              {error && (
                <p className="text-sm text-destructive" role="alert">{error}</p>
              )}

              <Button type="submit" className="w-full mt-2">
                시작하기
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

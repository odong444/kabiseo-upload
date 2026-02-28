"use client"

import { useState } from "react"
import useSWR from "swr"
import { adminFetcher } from "@/lib/api"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"

interface ChatSession {
  reviewer_id: string
  name: string
  phone: string
  message_count: number
  last_message: string
  last_time: string
}

interface ChatMessage {
  role: "user" | "bot"
  message: string
  timestamp: string
  rating?: string
}

export default function ChatHistoryPage() {
  const [search, setSearch] = useState("")
  const [selectedReviewer, setSelectedReviewer] = useState<string | null>(null)

  const { data: sessions } = useSWR<{ sessions: ChatSession[] }>(
    `/admin/api/chat-sessions?q=${encodeURIComponent(search)}`,
    adminFetcher
  )
  const { data: messages } = useSWR<{ messages: ChatMessage[] }>(
    selectedReviewer ? `/admin/api/chat-history/${encodeURIComponent(selectedReviewer)}` : null,
    adminFetcher
  )

  const sessionList = sessions?.sessions ?? []
  const messageList = messages?.messages ?? []

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">대화 이력</h1>
        <p className="text-sm text-muted-foreground mt-1">리뷰어 챗봇 대화 내역을 조회합니다</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Session List */}
        <Card className="lg:col-span-1">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">세션 목록</CardTitle>
            <Input
              placeholder="이름, 연락처 검색..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="mt-2"
            />
          </CardHeader>
          <CardContent className="p-0">
            <div className="max-h-[600px] overflow-y-auto">
              {sessionList.length === 0 ? (
                <p className="text-center py-8 text-muted-foreground text-sm">대화 세션이 없습니다</p>
              ) : (
                sessionList.map((session) => (
                  <button
                    key={session.reviewer_id}
                    onClick={() => setSelectedReviewer(session.reviewer_id)}
                    className={`w-full text-left px-4 py-3 border-b border-border/50 hover:bg-muted/50 transition-colors ${
                      selectedReviewer === session.reviewer_id ? "bg-primary/5 border-l-2 border-l-primary" : ""
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-foreground text-sm">{session.name}</span>
                      <Badge variant="secondary" className="text-xs">
                        {session.message_count}
                      </Badge>
                    </div>
                    <div className="text-xs text-muted-foreground mt-0.5">{session.phone}</div>
                    <div className="text-xs text-muted-foreground mt-1 truncate">{session.last_message}</div>
                    <div className="text-xs text-muted-foreground/60 mt-0.5">{session.last_time}</div>
                  </button>
                ))
              )}
            </div>
          </CardContent>
        </Card>

        {/* Message View */}
        <Card className="lg:col-span-2">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">
              {selectedReviewer ? `대화 내용` : "세션을 선택하세요"}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {!selectedReviewer ? (
              <p className="text-center py-16 text-muted-foreground">
                왼쪽에서 대화 세션을 선택하면 대화 내용이 표시됩니다
              </p>
            ) : messageList.length === 0 ? (
              <p className="text-center py-16 text-muted-foreground">대화 내역이 없습니다</p>
            ) : (
              <div className="space-y-3 max-h-[600px] overflow-y-auto">
                {messageList.map((msg, idx) => (
                  <div
                    key={idx}
                    className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
                  >
                    <div
                      className={`max-w-[75%] rounded-2xl px-4 py-2.5 ${
                        msg.role === "user"
                          ? "bg-primary text-primary-foreground rounded-br-md"
                          : "bg-muted text-foreground rounded-bl-md"
                      }`}
                    >
                      <p className="text-sm whitespace-pre-wrap break-words">{msg.message}</p>
                      <div className={`flex items-center gap-2 mt-1 ${
                        msg.role === "user" ? "justify-end" : "justify-start"
                      }`}>
                        <span className="text-[10px] opacity-60">{msg.timestamp}</span>
                        {msg.rating && (
                          <Badge
                            variant={msg.rating === "good" ? "default" : "destructive"}
                            className="text-[10px] px-1 py-0"
                          >
                            {msg.rating === "good" ? "Good" : "Bad"}
                          </Badge>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

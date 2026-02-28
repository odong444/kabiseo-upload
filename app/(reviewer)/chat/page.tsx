"use client"

import { useState, useRef, useEffect } from "react"
import { useAuth } from "@/lib/auth"
import { useChatSocket, type ChatMessage, type ChatButton, type ChatCard } from "@/lib/use-chat-socket"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Send, ChevronDown, ChevronUp, ExternalLink } from "lucide-react"
import { cn } from "@/lib/utils"

export default function ChatPage() {
  const { user } = useAuth()
  const { messages, isConnected, isTyping, sendMessage } = useChatSocket(
    user?.name || "",
    user?.phone || ""
  )

  const [input, setInput] = useState("")
  const [disabledGroups, setDisabledGroups] = useState<Set<string>>(new Set())
  const chatEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages, isTyping])

  function handleSend() {
    const text = input.trim()
    if (!text) return
    disableAllButtons()
    sendMessage(text)
    setInput("")
    if (textareaRef.current) textareaRef.current.style.height = "auto"
  }

  function handleButtonClick(value: string, label: string, groupId: string) {
    disableAllButtons()
    sendMessage(value, label)
  }

  function disableAllButtons() {
    const allIds = messages
      .filter((m) => m.buttons || m.cards || m.multi_select)
      .map((m) => m.id)
    setDisabledGroups(new Set(allIds))
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  function handleTextareaInput(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setInput(e.target.value)
    const el = e.target
    el.style.height = "auto"
    el.style.height = `${Math.min(el.scrollHeight, 84)}px`
  }

  return (
    <div className="flex h-[calc(100dvh-5rem)] flex-col">
      {/* Header */}
      <header className="flex items-center gap-3 border-b border-border bg-card px-4 py-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary text-sm font-bold text-primary-foreground">
          K
        </div>
        <div className="flex-1">
          <p className="text-sm font-semibold text-foreground">카비서</p>
          <p className="text-xs text-muted-foreground">
            {isConnected ? "온라인" : "연결 중..."}
          </p>
        </div>
      </header>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto bg-[oklch(0.92_0.02_230)] px-4 py-3">
        <div className="flex flex-col gap-2">
          {messages.map((msg) => (
            <MessageBubble
              key={msg.id}
              msg={msg}
              disabled={disabledGroups.has(msg.id)}
              onButton={handleButtonClick}
            />
          ))}

          {isTyping && <TypingIndicator />}
          <div ref={chatEndRef} />
        </div>
      </div>

      {/* Input */}
      <div className="border-t border-border bg-card px-4 py-3">
        <div className="flex items-end gap-2">
          <textarea
            ref={textareaRef}
            rows={1}
            value={input}
            onChange={handleTextareaInput}
            onKeyDown={handleKeyDown}
            placeholder="메시지를 입력하세요... (Shift+Enter: 전송)"
            className="flex-1 resize-none rounded-lg border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
          <Button
            size="icon"
            onClick={handleSend}
            disabled={!input.trim()}
            className="h-10 w-10 shrink-0"
            aria-label="메시지 전송"
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}

function MessageBubble({
  msg,
  disabled,
  onButton,
}: {
  msg: ChatMessage
  disabled: boolean
  onButton: (value: string, label: string, groupId: string) => void
}) {
  const isBot = msg.sender === "bot"
  const time = new Date(msg.timestamp * 1000)
  const timeStr = `${time.getHours().toString().padStart(2, "0")}:${time.getMinutes().toString().padStart(2, "0")}`

  return (
    <div className={cn("flex gap-2", isBot ? "items-start" : "items-start flex-row-reverse")}>
      {isBot && (
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
          K
        </div>
      )}
      <div className={cn("flex max-w-[75%] flex-col gap-1", isBot ? "items-start" : "items-end")}>
        {msg.message && (
          <div
            className={cn(
              "rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed",
              isBot
                ? "rounded-tl-md bg-card text-card-foreground shadow-sm"
                : "rounded-tr-md bg-primary text-primary-foreground"
            )}
          >
            <MessageContent text={msg.message} />
          </div>
        )}

        {/* Inline buttons */}
        {msg.buttons && msg.buttons.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-1">
            {msg.buttons.map((btn, i) => (
              <button
                key={i}
                disabled={disabled}
                onClick={() => onButton(btn.value, btn.label, msg.id)}
                className={cn(
                  "rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors",
                  disabled
                    ? "border-border bg-muted text-muted-foreground cursor-not-allowed opacity-50"
                    : btn.style === "danger"
                      ? "border-destructive bg-destructive/10 text-destructive hover:bg-destructive/20"
                      : btn.style === "secondary"
                        ? "border-border bg-card text-foreground hover:bg-accent"
                        : "border-primary bg-primary/10 text-primary-foreground hover:bg-primary/20"
                )}
              >
                {btn.label}
              </button>
            ))}
          </div>
        )}

        {/* Campaign cards */}
        {msg.cards && msg.cards.length > 0 && (
          <div className="flex flex-col gap-2 mt-1 w-full">
            {msg.cards.map((card, i) => (
              <CampaignCardBubble
                key={i}
                card={card}
                disabled={disabled}
                onSelect={(value, label) => onButton(value, label, msg.id)}
              />
            ))}
          </div>
        )}

        <span className="text-[10px] text-muted-foreground px-1">{timeStr}</span>
      </div>
    </div>
  )
}

function MessageContent({ text }: { text: string }) {
  // Parse [IMG:url] tags and links
  const parts: React.ReactNode[] = []
  let remaining = text

  // Extract images
  const imgRegex = /\[IMG:(.*?)\]/g
  let imgMatch
  const images: string[] = []
  while ((imgMatch = imgRegex.exec(text)) !== null) {
    images.push(imgMatch[1])
  }
  remaining = remaining.replace(/\[IMG:(.*?)\]/g, "")

  // Simple link detection and render
  const linkRegex = /(https?:\/\/[^\s<]+)/g
  const segments = remaining.split(linkRegex)
  segments.forEach((seg, i) => {
    if (linkRegex.test(seg)) {
      parts.push(
        <a key={i} href={seg} target="_blank" rel="noopener noreferrer" className="underline text-[oklch(0.5_0.15_250)]">
          {seg}
        </a>
      )
    } else {
      // Handle newlines
      const lines = seg.split("\n")
      lines.forEach((line, j) => {
        if (j > 0) parts.push(<br key={`br-${i}-${j}`} />)
        parts.push(line)
      })
    }
    linkRegex.lastIndex = 0
  })

  return (
    <>
      {images.map((url, i) => (
        <img key={`img-${i}`} src={driveToEmbed(url)} alt="" className="max-w-full rounded-lg mt-1" loading="lazy" crossOrigin="anonymous" />
      ))}
      {parts}
    </>
  )
}

function driveToEmbed(url: string) {
  const match = url.match(/\/file\/d\/([^/]+)/)
  if (match) return `https://drive.google.com/thumbnail?id=${match[1]}&sz=w400`
  return url
}

function CampaignCardBubble({
  card,
  disabled,
  onSelect,
}: {
  card: ChatCard
  disabled: boolean
  onSelect: (value: string, label: string) => void
}) {
  const [open, setOpen] = useState(false)
  const isClosed = card.closed || card.buy_time_closed

  return (
    <div className={cn("rounded-xl border bg-card shadow-sm overflow-hidden", isClosed && "opacity-60")}>
      <button
        className="flex w-full items-center justify-between px-3 py-2.5 text-left"
        onClick={() => setOpen(!open)}
      >
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-foreground truncate">{card.name}</p>
          <div className="flex items-center gap-1.5 mt-0.5">
            {isClosed ? (
              <Badge variant="secondary" className="text-[10px]">{card.closed_reason || "마감"}</Badge>
            ) : (
              <Badge variant="default" className="text-[10px]">{card.remaining}자리</Badge>
            )}
            {card.urgent && !isClosed && (
              <Badge variant="destructive" className="text-[10px]">긴급</Badge>
            )}
          </div>
        </div>
        {open ? <ChevronUp className="h-3.5 w-3.5 text-muted-foreground" /> : <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />}
      </button>

      {open && (
        <div className="border-t border-border px-3 py-2.5">
          <div className="flex flex-col gap-1 text-xs text-muted-foreground">
            <span>{card.store}</span>
            {card.product_price && <span>상품금액: {card.product_price}</span>}
            {card.review_fee && <span>리뷰비: {card.review_fee}</span>}
            {card.platform && <span>{card.platform}</span>}
            <span>남은자리: {card.remaining} / {card.total || card.remaining}</span>
            {card.buy_time && <span>구매시간: {card.buy_time}</span>}
          </div>

          {card.my_history && card.my_history.length > 0 && (
            <div className="mt-2 border-t border-border pt-2">
              <p className="text-xs font-medium text-foreground mb-1">내 진행 이력:</p>
              {card.my_history.map((h, i) => (
                <p key={i} className="text-xs text-muted-foreground">{h.id} - {h.status}</p>
              ))}
            </div>
          )}

          {!isClosed && (
            <button
              disabled={disabled}
              onClick={() => onSelect(card.value, card.name)}
              className={cn(
                "mt-2 w-full rounded-lg px-3 py-2 text-xs font-medium transition-colors",
                disabled
                  ? "bg-muted text-muted-foreground cursor-not-allowed"
                  : "bg-primary text-primary-foreground hover:bg-primary/90"
              )}
            >
              신청하기
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function TypingIndicator() {
  return (
    <div className="flex items-start gap-2">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
        K
      </div>
      <div className="rounded-2xl rounded-tl-md bg-card px-4 py-3 shadow-sm">
        <div className="flex gap-1">
          <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground" style={{ animationDelay: "0ms" }} />
          <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground" style={{ animationDelay: "150ms" }} />
          <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground" style={{ animationDelay: "300ms" }} />
        </div>
      </div>
    </div>
  )
}

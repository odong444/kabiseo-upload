"use client"

import { useEffect, useRef, useState, useCallback } from "react"
import { io, type Socket } from "socket.io-client"
import { getApiBase } from "@/lib/api"

export interface ChatMessage {
  id: string
  sender: "user" | "bot"
  message: string
  timestamp: number
  buttons?: ChatButton[]
  cards?: ChatCard[]
  multi_select?: ChatMultiSelect
}

export interface ChatButton {
  label: string
  value: string
  style?: "danger" | "secondary"
}

export interface ChatCard {
  name: string
  store: string
  value: string
  remaining: number
  total?: number
  product_price?: string
  review_fee?: string
  platform?: string
  buy_time?: string
  closed?: boolean
  closed_reason?: string
  buy_time_closed?: boolean
  urgent?: boolean
  my_history?: { id: string; status: string }[]
}

export interface ChatMultiSelect {
  max_select: number
  items: { id: string; disabled?: boolean; reason?: string }[]
}

let msgCounter = 0
function makeId() {
  return `msg_${Date.now()}_${++msgCounter}`
}

export function useChatSocket(name: string, phone: string) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isConnected, setIsConnected] = useState(false)
  const [isTyping, setIsTyping] = useState(false)
  const socketRef = useRef<Socket | null>(null)

  useEffect(() => {
    if (!name || !phone) return

    const socket = io(getApiBase(), {
      transports: ["websocket", "polling"],
    })
    socketRef.current = socket

    socket.on("connect", () => {
      setIsConnected(true)
      socket.emit("join", { name, phone })
    })

    socket.on("disconnect", () => {
      setIsConnected(false)
    })

    socket.on("chat_history", (data: { messages: { sender: string; message: string; timestamp: number }[] }) => {
      const history: ChatMessage[] = (data.messages || []).map((m) => ({
        id: makeId(),
        sender: m.sender as "user" | "bot",
        message: m.message,
        timestamp: m.timestamp,
      }))
      setMessages(history)
    })

    socket.on("bot_message", (data: {
      message?: string
      buttons?: ChatButton[]
      cards?: ChatCard[]
      multi_select?: ChatMultiSelect
    }) => {
      setIsTyping(false)
      const msg: ChatMessage = {
        id: makeId(),
        sender: "bot",
        message: data.message || "",
        timestamp: Date.now() / 1000,
        buttons: data.buttons,
        cards: data.cards,
        multi_select: data.multi_select,
      }
      setMessages((prev) => [...prev, msg])
    })

    socket.on("bot_typing", (data: { typing: boolean }) => {
      setIsTyping(data.typing)
    })

    socket.on("error", (data: { message: string }) => {
      setMessages((prev) => [
        ...prev,
        { id: makeId(), sender: "bot", message: data.message || "오류가 발생했습니다.", timestamp: Date.now() / 1000 },
      ])
    })

    return () => {
      socket.disconnect()
      socketRef.current = null
    }
  }, [name, phone])

  const sendMessage = useCallback(
    (text: string, displayText?: string) => {
      if (!socketRef.current || !text.trim()) return

      const userMsg: ChatMessage = {
        id: makeId(),
        sender: "user",
        message: displayText || text,
        timestamp: Date.now() / 1000,
      }
      setMessages((prev) => [...prev, userMsg])

      socketRef.current.emit("user_message", {
        name,
        phone,
        message: text,
      })
    },
    [name, phone]
  )

  return { messages, isConnected, isTyping, sendMessage }
}

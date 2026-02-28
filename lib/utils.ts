import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatCurrency(amount: number | string): string {
  const num = typeof amount === "string" ? parseInt(amount.replace(/[^0-9]/g, ""), 10) : amount
  if (isNaN(num)) return "0"
  return num.toLocaleString("ko-KR")
}

export function formatPhone(phone: string): string {
  const cleaned = phone.replace(/[^0-9]/g, "")
  if (cleaned.length === 11) {
    return `${cleaned.slice(0, 3)}-${cleaned.slice(3, 7)}-${cleaned.slice(7)}`
  }
  return phone
}

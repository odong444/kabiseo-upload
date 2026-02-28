import { Badge } from "@/components/ui/badge"
import type { TaskStatus } from "@/lib/types"

const STATUS_CONFIG: Record<string, { variant: "default" | "secondary" | "destructive" | "outline"; label?: string }> = {
  "신청": { variant: "outline" },
  "가이드전달": { variant: "default" },
  "구매캡쳐대기": { variant: "default", label: "구매 캡쳐 대기" },
  "리뷰대기": { variant: "default", label: "리뷰 대기" },
  "리뷰제출": { variant: "secondary", label: "리뷰 제출" },
  "입금대기": { variant: "secondary", label: "입금 대기" },
  "입금완료": { variant: "default", label: "입금 완료" },
  "타임아웃취소": { variant: "destructive", label: "타임아웃" },
  "취소": { variant: "destructive" },
}

export function TaskStatusBadge({ status }: { status: TaskStatus }) {
  const config = STATUS_CONFIG[status] || { variant: "outline" as const }
  return (
    <Badge variant={config.variant}>{config.label || status}</Badge>
  )
}

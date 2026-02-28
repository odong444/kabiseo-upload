export interface User {
  name: string
  phone: string
}

export interface Campaign {
  campaign_id: string
  name: string
  store: string
  total: number
  remaining: number
  urgent: boolean
  buy_time: string
  buy_time_closed: boolean
  product_price: string
  review_fee: string
  platform: string
  closed: boolean
  closed_reason: string
  max_per_person_daily: number
  my_history?: { id: string; status: string; progress_id?: number }[]
}

export interface CampaignDetail {
  campaign_id: string
  name: string
  store: string
  product_name: string
  product_link: string
  product_image: string
  product_price: string | number
  review_fee: string | number
  platform: string
  total: number
  remaining: number
  daily_remaining: number
  keyword: string
  options: string
  option_list: string
  buy_time: string
  buy_time_active: boolean
  status: string
  campaign_guide: string
  review_guide: string
  extra_info: string
  campaign_type: string
  payment_amount: string | number
  dwell_time: string
  bookmark_required: string
  alert_required: string
  entry_method: string
  ship_memo_required: string
  ship_memo_content: string
  ship_memo_link: string
  max_per_person_daily: number
  my_ids?: string[]
}

export type TaskStatus =
  | "신청"
  | "가이드전달"
  | "구매캡쳐대기"
  | "리뷰대기"
  | "리뷰제출"
  | "입금대기"
  | "입금완료"
  | "타임아웃취소"
  | "취소"

export interface TaskItem {
  id: number
  campaign_id: string
  product_name: string
  store_name: string
  store_id: string
  status: TaskStatus
  date: string
  purchase_capture?: string
  review_capture?: string
  review_fee: string | number
  remark: string
}

export interface TaskDetail {
  id: number
  campaign_id: string
  product_name: string
  store_name: string
  store_id: string
  status: TaskStatus
  date: string
  created_at: string
  timeout_seconds: number
  purchase_capture: string
  review_capture: string
  remark: string
  recipient_name: string
  phone: string
  bank: string
  account: string
  depositor: string
  address: string
  order_number: string
  payment_amount: string | number
  nickname: string
  prev_info: {
    recipientName?: string
    phone?: string
    address?: string
    bank?: string
    account?: string
    depositor?: string
  }
  bank_presets: { bank: string; account: string; depositor: string }[]
  campaign: {
    name: string
    product_name: string
    product_link: string
    product_image: string
    product_codes: { codes?: Record<string, string> }
    platform: string
    keyword: string
    options: string
    campaign_guide: string
    review_guide: string
    extra_info: string
    campaign_type: string
    payment_amount: string | number
    dwell_time: string
    bookmark_required: string
    alert_required: string
    entry_method: string
    ship_memo_required: string
    ship_memo_content: string
    ship_memo_link: string
    review_fee: string | number
  }
  siblings: {
    id: number
    store_id: string
    status: TaskStatus
    order_number?: string
    recipient_name?: string
    phone?: string
    payment_amount?: string | number
    address?: string
    bank?: string
    account?: string
    depositor?: string
    nickname?: string
    remark?: string
  }[]
  photo_set: string[]
}

export interface PaymentItem {
  productName: string
  storeId: string
  amount: number
  date: string
  reviewSubmitDate?: string
  reviewDeadline?: string
}

export interface PaymentResponse {
  paid: PaymentItem[]
  pending: PaymentItem[]
  no_review: PaymentItem[]
}

export interface ApplyResult {
  ok: boolean
  results: { store_id: string; ok: boolean; error?: string; progress_id?: number }[]
  message: string
}

export interface StatusResponse {
  in_progress: TaskItem[]
  completed: TaskItem[]
}

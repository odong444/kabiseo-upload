const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:5001"

interface FetchOptions extends RequestInit {
  params?: Record<string, string>
}

export async function apiClient<T>(path: string, options?: FetchOptions): Promise<T> {
  let url = `${API_BASE}${path}`

  if (options?.params) {
    const searchParams = new URLSearchParams()
    for (const [key, value] of Object.entries(options.params)) {
      if (value) searchParams.set(key, value)
    }
    const qs = searchParams.toString()
    if (qs) url += `?${qs}`
  }

  const { params: _, ...fetchOpts } = options || {}

  const res = await fetch(url, {
    ...fetchOpts,
    headers: {
      ...(fetchOpts.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...fetchOpts.headers,
    },
    credentials: "include",
  })

  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({ error: `HTTP ${res.status}` }))
    throw new Error(errorBody.error || `API Error: ${res.status}`)
  }

  return res.json()
}

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`
}

export function getApiBase(): string {
  return API_BASE
}

// SWR fetcher for admin pages
export async function adminFetcher<T>(path: string): Promise<T> {
  const url = `${API_BASE}${path}`
  const res = await fetch(url, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
  })
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({ error: `HTTP ${res.status}` }))
    throw new Error(errorBody.error || `API Error: ${res.status}`)
  }
  return res.json()
}

// Simple admin API call helper (for mutations)
export async function adminApi(path: string, options?: RequestInit) {
  const url = `${API_BASE}${path}`
  const res = await fetch(url, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  })
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({ error: `HTTP ${res.status}` }))
    throw new Error(errorBody.error || `API Error: ${res.status}`)
  }
  return res.json()
}

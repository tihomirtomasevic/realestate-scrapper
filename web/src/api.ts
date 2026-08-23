import type {
  CrawlStatus, Filters, GoneFilters, GoneListing, ListingDetail, ListingSummary,
  Page, PriceDrop, ReviewPair, Stats,
} from './types'

// Same-origin: nginx proxies /api to the API container, so no CORS in prod.
const BASE = '/api'

async function get<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v === undefined || v === null || v === '') continue
    qs.append(k, String(v))
  }
  const url = qs.toString() ? `${BASE}${path}?${qs}` : `${BASE}${path}`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`)
  return res.json() as Promise<T>
}

export const api = {
  listings: (filters: Filters, page: number, pageSize = 50) =>
    get<Page<ListingSummary>>('/listings', {
      q: filters.q || undefined,
      min_price: filters.min_price,
      max_price: filters.max_price,
      min_area: filters.min_area,
      max_area: filters.max_area,
      rooms: filters.rooms,
      location: filters.location,
      seller_type: filters.seller_type,
      active_only: filters.active_only,
      collapse_duplicates: filters.collapse_duplicates,
      // Relevance ordering only means anything with a query present.
      sort: filters.sort === 'relevance' && !filters.q ? 'newest' : filters.sort,
      page,
      page_size: pageSize,
    }),

  listing: (id: number) => get<ListingDetail>(`/listings/${id}`),

  priceDrops: (minDropPct: number, days: number, limit = 50) =>
    get<PriceDrop[]>('/price-drops', { min_drop_pct: minDropPct, days, limit }),

  gone: (f: GoneFilters, page: number, pageSize = 50) =>
    get<Page<GoneListing>>('/gone', {
      sort: f.sort,
      days: f.days,
      reason: f.reason,
      max_days_listed: f.max_days_listed,
      hide_still_listed: f.hide_still_listed,
      page,
      page_size: pageSize,
    }),

  stats: () => get<Stats>('/stats'),

  reviewQueue: (limit = 50) => get<ReviewPair[]>('/dedup/review', { limit }),

  crawlStatus: () => get<CrawlStatus>('/crawl/status'),

  startCrawl: async (source?: string) => {
    const qs = source ? `?source=${encodeURIComponent(source)}` : ''
    const res = await fetch(`${BASE}/crawl${qs}`, { method: 'POST' })
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
    return res.json()
  },

  linkDuplicate: async (a: number, b: number, label: 'same' | 'different' = 'same') => {
    const res = await fetch(`${BASE}/dedup/link`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ listing_a: a, listing_b: b, label }),
    })
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
    return res.json()
  },

  labelPair: async (pairId: number, label: 'same' | 'different') => {
    const res = await fetch(`${BASE}/dedup/review/${pairId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label }),
    })
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
    return res.json()
  },
}

const eur = new Intl.NumberFormat('hr-HR', {
  style: 'currency', currency: 'EUR', maximumFractionDigits: 0,
})

export const fmtPrice = (v: string | null | undefined) =>
  v == null ? '—' : eur.format(Number(v))

export const fmtNum = (v: string | number | null | undefined, suffix = '') =>
  v == null ? '—' : `${Number(v).toLocaleString('hr-HR', { maximumFractionDigits: 1 })}${suffix}`

export const fmtDate = (v: string | null | undefined) =>
  v == null ? '—' : new Date(v).toLocaleDateString('hr-HR')

export const daysAgo = (v: string) =>
  Math.floor((Date.now() - new Date(v).getTime()) / 86_400_000)

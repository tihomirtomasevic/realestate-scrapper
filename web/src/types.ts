export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface ListingSummary {
  listing_id: number
  cluster_id: number | null
  source: string
  url: string
  title: string | null
  price_eur: string | null
  area_m2: string | null
  price_per_m2: string | null
  rooms: string | null
  floor: string | null
  location_raw: string | null
  seller_type: 'private' | 'agency' | null
  seller_name: string | null
  active: boolean
  first_seen: string
  last_seen: string
  captured_at: string
  duplicate_count: number
  thumbnail: string | null
  ai_score: number | null
  ai_verdict: string | null
  gated: boolean
  gate_unfinished: boolean
  gate_ruin: boolean
  gate_needs_adaptation: boolean
  gate_severity: 'cosmetic' | 'partial' | 'full' | null
}

export interface PricePoint {
  captured_at: string
  price_eur: string
  listing_id: number
}

export interface ListingDetail extends ListingSummary {
  description: string | null
  lat: number | null
  lon: number | null
  seller_phone: string | null
  price_history: PricePoint[]
  images: { url: string; phash: string | null; position: number | null }[]
  ai: {
    model: string
    score: number | null
    verdict: string | null
    reasoning: string | null
    flags: string[] | null
    created_at: string
  } | null
  duplicates: ListingSummary[]
}

export interface PriceDrop {
  cluster_id: number
  listing_id: number
  source: string
  url: string
  title: string | null
  location_raw: string | null
  area_m2: string | null
  first_price: string
  latest_price: string
  change_pct: number
  change_eur: string
  listing_count: number
  first_seen: string
  last_seen: string
  price_history: PricePoint[]
}

export interface Stats {
  listings_total: number
  listings_active: number
  clusters_total: number
  duplicates_collapsed: number
  observations_total: number
  price_drops_30d: number
  listings_gone: number
  listings_missing: number
  listings_out_of_scope: number
  review_queue: number
  last_crawl: string | null
  sources: string[]
}

export interface ReviewPair {
  pair_id: number
  score: number
  listing_a: number
  listing_b: number
  url_a: string
  url_b: string
  title_a: string | null
  title_b: string | null
  price_a: string | null
  price_b: string | null
  area_a: string | null
  area_b: string | null
  img_match_count: number
  text_jaccard: number | null
  floor_conflict: boolean
  phone_match: boolean
  evidence: Record<string, unknown> | null
}

export type SortField =
  | 'newest' | 'oldest' | 'price_asc' | 'price_desc'
  | 'price_per_m2' | 'price_per_m2_desc'
  | 'area_desc' | 'area_asc' | 'relevance'

export interface Filters {
  q: string
  min_price: string
  max_price: string
  min_area: string
  max_area: string
  rooms: string
  location: string
  seller_type: string
  active_only: boolean
  collapse_duplicates: boolean
  /** Hide unfinished builds, ruins and adaptation projects. */
  hide_gated: boolean
  sort: SortField
}

export const EMPTY_FILTERS: Filters = {
  q: '', min_price: '', max_price: '', min_area: '', max_area: '',
  rooms: '', location: '', seller_type: '',
  active_only: true, collapse_duplicates: true, hide_gated: true, sort: 'newest',
}

export interface CrawlRequest {
  id: number
  source: string | null
  requested_by: string
  status: 'pending' | 'running' | 'done' | 'failed' | 'cancelled'
  requested_at: string
  started_at: string | null
  finished_at: string | null
  result: Record<string, unknown> | null
  error: string | null
}

export interface CrawlStatus {
  active: CrawlRequest | null
  recent: CrawlRequest[]
  last_run: {
    source: string
    finished_at: string | null
    pages: number
    requests: number
    new_ads: number
    updated_ads: number
    error: string | null
  } | null
}

export type GoneSort =
  | 'recent' | 'fastest' | 'slowest' | 'best_value' | 'biggest_drop'
  | 'price_asc' | 'price_desc'

/** An ad that stopped running.
 *
 *  Neither portal marks real estate as sold, so `status_reason` says only how
 *  the ad ended, never why. The numbers around it — how long it ran, what the
 *  price did, how it sat against the local rate — are the evidence for reading
 *  a disappearance as a sale.
 */
export interface GoneListing {
  listing_id: number
  cluster_id: number | null
  source: string
  url: string
  title: string | null
  location_raw: string | null
  price_eur: string | null
  area_m2: string | null
  rooms: string | null
  price_per_m2: string | null
  seller_type: string | null
  seller_name: string | null
  status_reason: string | null
  first_seen: string
  last_seen: string
  removed_at: string | null
  days_listed: string | null
  first_price: string | null
  last_price: string | null
  price_change_pct: number | null
  median_price_per_m2: string | null
  vs_area_median_pct: number | null
  still_listed_elsewhere: boolean
  thumbnail: string | null
}

export interface GoneFilters {
  sort: GoneSort
  days: number
  reason?: string
  max_days_listed?: number
  hide_still_listed: boolean
}

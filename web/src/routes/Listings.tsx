import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { FilterBar } from '../components/FilterBar'
import { ResultsTable } from '../components/ResultsTable'
import { EMPTY_FILTERS, type Filters, type ListingSummary, type SortField } from '../types'

/** Filters live in the URL so a search is shareable and survives reload. */
function fromParams(sp: URLSearchParams): Filters {
  return {
    ...EMPTY_FILTERS,
    q: sp.get('q') ?? '',
    min_price: sp.get('min_price') ?? '',
    max_price: sp.get('max_price') ?? '',
    min_area: sp.get('min_area') ?? '',
    max_area: sp.get('max_area') ?? '',
    rooms: sp.get('rooms') ?? '',
    location: sp.get('location') ?? '',
    seller_type: sp.get('seller_type') ?? '',
    active_only: sp.get('active_only') !== 'false',
    collapse_duplicates: sp.get('collapse_duplicates') !== 'false',
    hide_gated: sp.get('hide_gated') !== 'false',
    sort: (sp.get('sort') as SortField) ?? 'newest',
  }
}

export function Listings() {
  const [sp, setSp] = useSearchParams()
  const filters = useMemo(() => fromParams(sp), [sp])
  const page = Number(sp.get('page') ?? 1)

  const [rows, setRows] = useState<ListingSummary[]>([])
  const [total, setTotal] = useState(0)
  const [pages, setPages] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const write = (next: Partial<Filters> & { page?: number }, resetPage = true) => {
    const merged = { ...filters, ...next }
    const out = new URLSearchParams()
    for (const [k, v] of Object.entries(merged)) {
      if (k === 'page') continue
      const def = EMPTY_FILTERS[k as keyof Filters]
      if (v !== def && v !== '') out.set(k, String(v))
    }
    const p = resetPage ? 1 : (next.page ?? page)
    if (p > 1) out.set('page', String(p))
    setSp(out, { replace: true })
  }

  // Debounce so typing in the text box does not fire a request per keystroke.
  const timer = useRef<number>()
  useEffect(() => {
    setLoading(true)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      api.listings(filters, page)
        .then((res) => {
          setRows(res.items); setTotal(res.total); setPages(res.pages); setError(null)
        })
        .catch((e) => setError(String(e)))
        .finally(() => setLoading(false))
    }, 250)
    return () => window.clearTimeout(timer.current)
  }, [sp])

  return (
    <div className="stack">
      <FilterBar
        value={filters}
        onChange={(next) => write(next)}
        onReset={() => setSp(new URLSearchParams(), { replace: true })}
        total={total}
      />

      {error && <div className="error">Failed to load listings: {error}</div>}

      <div style={{ opacity: loading ? 0.55 : 1, transition: 'opacity .15s' }}>
        <ResultsTable rows={rows} sort={filters.sort} onSort={(s) => write({ sort: s })} />
      </div>

      {pages > 1 && (
        <div className="pager">
          <button className="ghost" disabled={page <= 1}
                  onClick={() => write({ page: page - 1 }, false)}>← Prev</button>
          <span className="muted">Page {page} of {pages}</span>
          <button className="ghost" disabled={page >= pages}
                  onClick={() => write({ page: page + 1 }, false)}>Next →</button>
        </div>
      )}
    </div>
  )
}

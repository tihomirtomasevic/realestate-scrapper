import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtDate, fmtNum, fmtPrice } from '../api'
import type { GoneFilters, GoneListing } from '../types'

/** How an ad ended, in the site's own words. */
const REASON_LABEL: Record<string, string> = {
  expired: 'expired',
  deleted: 'withdrawn',
  not_listed: 'delisted',
}

/** Days on the market, bucketed.
 *
 *  A house that went in under a month is the interesting one: it was priced
 *  where the market actually was. Colour follows that reading.
 */
function speedClass(days: number | null): string {
  if (days === null) return ''
  if (days <= 30) return 'drop-pct'
  if (days <= 90) return ''
  return 'muted'
}

export function Gone() {
  const [f, setF] = useState<GoneFilters>({
    sort: 'recent', days: 365, hide_still_listed: true,
  })
  const [rows, setRows] = useState<GoneListing[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    api.gone(f, 1)
      .then((r) => { setRows(r.items); setTotal(r.total); setError(null) })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [f])

  const set = <K extends keyof GoneFilters>(k: K, v: GoneFilters[K]) =>
    setF((prev) => ({ ...prev, [k]: v }))

  return (
    <div className="stack">
      <div className="panel">
        <div className="between">
          <div>
            <strong>Off the market</strong>
            <div className="muted" style={{ fontSize: 13, marginTop: 2, maxWidth: 620 }}>
              Ads that stopped running. Neither site says whether a house sold or the
              seller simply gave up, so this shows the evidence instead: how long it
              was advertised, what the price did, and how it compared with everything
              else in the same place. A house that went quickly and below the local
              rate is the one worth learning from.
            </div>
          </div>
          <div className="row">
            <div>
              <label htmlFor="gsort">Sort</label>
              <select id="gsort" value={f.sort}
                      onChange={(e) => set('sort', e.target.value as GoneFilters['sort'])}>
                <option value="recent">Ended most recently</option>
                <option value="fastest">Went fastest</option>
                <option value="slowest">Sat longest</option>
                <option value="best_value">Cheapest vs area</option>
                <option value="biggest_drop">Biggest price cut</option>
                <option value="price_asc">Price, low to high</option>
                <option value="price_desc">Price, high to low</option>
              </select>
            </div>
            <div>
              <label htmlFor="gdays">Ended within</label>
              <select id="gdays" value={f.days}
                      onChange={(e) => set('days', Number(e.target.value))}>
                <option value={30}>30 days</option>
                <option value={90}>90 days</option>
                <option value={365}>1 year</option>
                <option value={3650}>Any time</option>
              </select>
            </div>
            <div>
              <label htmlFor="greason">Ending</label>
              <select id="greason" value={f.reason ?? ''}
                      onChange={(e) => set('reason', e.target.value || undefined)}>
                <option value="">Any</option>
                <option value="expired">Expired</option>
                <option value="deleted">Withdrawn</option>
                <option value="not_listed">Delisted</option>
              </select>
            </div>
          </div>
        </div>
        <div className="checks" style={{ marginTop: 10 }}>
          <label>
            <input type="checkbox" checked={f.hide_still_listed}
                   onChange={(e) => set('hide_still_listed', e.target.checked)} />
            Hide properties still advertised elsewhere
            <span className="muted">
              {' '}— one ad of several ending says nothing about the sale
            </span>
          </label>
        </div>
      </div>

      {error && <div className="error">Failed to load: {error}</div>}

      {!error && !loading && rows.length === 0 && (
        <div className="panel empty">
          Nothing here yet. An ad only lands in this list once it has dropped out of
          the search <em>and</em> its own page has confirmed it ended — so this fills
          in over weeks, as listings you have been tracking come off the market.
        </div>
      )}

      {rows.length > 0 && (
        <div className="panel table-wrap" style={{ padding: 0, opacity: loading ? 0.55 : 1 }}>
          <table>
            <thead>
              <tr>
                <th>Title</th>
                <th>Location</th>
                <th className="num">Last price</th>
                <th className="num">€ / m²</th>
                <th className="num">vs area</th>
                <th className="num">Price move</th>
                <th className="num">Days listed</th>
                <th>Ending</th>
                <th className="num">Ended</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const days = r.days_listed === null ? null : Number(r.days_listed)
                return (
                  <tr key={r.listing_id}>
                    <td>
                      <Link to={`/listing/${r.listing_id}`}>{r.title ?? '(untitled)'}</Link>
                      <span className="badge" style={{ marginLeft: 6 }}>{r.source}</span>
                      {r.still_listed_elsewhere && (
                        <span className="badge dup" style={{ marginLeft: 4 }}>
                          still listed elsewhere
                        </span>
                      )}
                    </td>
                    <td>{r.location_raw ?? '—'}</td>
                    <td className="num">{fmtPrice(r.last_price ?? r.price_eur)}</td>
                    <td className="num">{fmtNum(r.price_per_m2)}</td>
                    <td className="num">
                      {r.vs_area_median_pct === null ? '—' : (
                        <span className={r.vs_area_median_pct < 0 ? 'drop-pct' : 'muted'}>
                          {r.vs_area_median_pct > 0 ? '+' : ''}
                          {r.vs_area_median_pct.toFixed(0)}%
                        </span>
                      )}
                    </td>
                    <td className="num">
                      {r.price_change_pct ? (
                        <span className={r.price_change_pct < 0 ? 'drop-pct' : 'muted'}>
                          {r.price_change_pct > 0 ? '+' : ''}
                          {r.price_change_pct.toFixed(1)}%
                        </span>
                      ) : '—'}
                    </td>
                    <td className={`num ${speedClass(days)}`}>
                      {days === null ? '—' : days.toFixed(0)}
                    </td>
                    <td>
                      <span className="badge">
                        {REASON_LABEL[r.status_reason ?? ''] ?? r.status_reason ?? '—'}
                      </span>
                    </td>
                    <td className="num">{fmtDate(r.removed_at ?? r.last_seen)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {total > rows.length && (
        <div className="muted">Showing {rows.length} of {total}.</div>
      )}
    </div>
  )
}

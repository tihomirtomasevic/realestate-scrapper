import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtDate, fmtNum, fmtPrice } from '../api'
import { Sparkline } from '../components/Sparkline'
import type { PriceDrop } from '../types'

export function PriceDrops() {
  const [minDrop, setMinDrop] = useState(2)
  const [days, setDays] = useState(90)
  const [rows, setRows] = useState<PriceDrop[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    api.priceDrops(minDrop, days)
      .then((r) => { setRows(r); setError(null) })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [minDrop, days])

  return (
    <div className="stack">
      <div className="panel">
        <div className="between">
          <div>
            <strong>Price drops</strong>
            <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>
              Tracked per property, not per ad — a seller who deletes a listing and
              reposts it cheaper still shows up here.
            </div>
          </div>
          <div className="row">
            <div>
              <label htmlFor="mindrop">Min drop</label>
              <select id="mindrop" value={minDrop} onChange={(e) => setMinDrop(Number(e.target.value))}>
                <option value={0}>Any</option>
                <option value={2}>2%</option>
                <option value={5}>5%</option>
                <option value={10}>10%</option>
                <option value={15}>15%</option>
              </select>
            </div>
            <div>
              <label htmlFor="days">Window</label>
              <select id="days" value={days} onChange={(e) => setDays(Number(e.target.value))}>
                <option value={7}>7 days</option>
                <option value={30}>30 days</option>
                <option value={90}>90 days</option>
                <option value={365}>1 year</option>
              </select>
            </div>
          </div>
        </div>
      </div>

      {error && <div className="error">Failed to load price drops: {error}</div>}

      {!error && !loading && rows.length === 0 && (
        <div className="panel empty">
          No price drops recorded yet. This needs at least two crawls of the same
          ad at different prices, so it fills in over days, not minutes.
        </div>
      )}

      {rows.length > 0 && (
        <div className="panel table-wrap" style={{ padding: 0, opacity: loading ? 0.55 : 1 }}>
          <table>
            <thead>
              <tr>
                <th>Title</th>
                <th>Location</th>
                <th className="num">Was</th>
                <th className="num">Now</th>
                <th className="num">Change</th>
                <th className="num">Area</th>
                <th>History</th>
                <th className="num">Last seen</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.cluster_id}>
                  <td>
                    <Link to={`/listing/${d.listing_id}`}>{d.title ?? '(untitled)'}</Link>
                    <div className="row" style={{ gap: 6, marginTop: 3 }}>
                      <span className="badge">{d.source}</span>
                      {d.listing_count > 1 && (
                        <span className="badge dup"
                              title="Seen under more than one ad — reposted or listed by several agencies">
                          {d.listing_count} ads
                        </span>
                      )}
                    </div>
                  </td>
                  <td>{d.location_raw ?? '—'}</td>
                  <td className="num muted" style={{ textDecoration: 'line-through' }}>
                    {fmtPrice(d.first_price)}
                  </td>
                  <td className="num"><strong>{fmtPrice(d.latest_price)}</strong></td>
                  <td className="num">
                    <span className="drop-pct">{d.change_pct.toFixed(1)}%</span>
                    <div className="muted" style={{ fontSize: 12 }}>{fmtPrice(d.change_eur)}</div>
                  </td>
                  <td className="num">{fmtNum(d.area_m2, ' m²')}</td>
                  <td><Sparkline points={d.price_history} /></td>
                  <td className="num">{fmtDate(d.last_seen)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

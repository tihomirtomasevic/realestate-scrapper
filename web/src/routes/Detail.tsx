import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, fmtDate, fmtNum, fmtPrice } from '../api'
import { Gallery } from '../components/Gallery'
import { LinkDuplicate } from '../components/LinkDuplicate'
import { Sparkline } from '../components/Sparkline'
import type { ListingDetail } from '../types'

export function Detail() {
  const { id } = useParams()
  const [item, setItem] = useState<ListingDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(
    () => api.listing(Number(id)).then(setItem).catch((e) => setError(String(e))),
    [id],
  )

  useEffect(() => { load() }, [load])

  if (error) return <div className="error">Failed to load listing: {error}</div>
  if (!item) return <div className="panel empty">Loading…</div>

  const first = item.price_history[0]
  const last = item.price_history[item.price_history.length - 1]
  const changePct = first && last && Number(first.price_eur)
    ? ((Number(last.price_eur) - Number(first.price_eur)) / Number(first.price_eur)) * 100
    : null

  return (
    <div className="stack">
      <div className="panel stack">
        <div className="between">
          <div>
            <h2 style={{ margin: '0 0 6px' }}>{item.title ?? '(untitled)'}</h2>
            <div className="row" style={{ gap: 8 }}>
              <span className="badge">{item.source}</span>
              {!item.active && <span className="badge inactive">removed from site</span>}
              {item.duplicate_count > 1 && (
                <span className="badge dup">{item.duplicate_count} source ads</span>
              )}
              <span className="muted">{item.location_raw ?? '—'}</span>
            </div>
          </div>
          <a href={item.url} target="_blank" rel="noopener noreferrer">Open original ↗</a>
        </div>

        <div className="grid-2">
          <div className="stack">
            <Gallery images={item.images} alt={item.title ?? ''} />
            <table>
              <tbody>
                <tr><th>Price</th><td className="num">{fmtPrice(item.price_eur)}</td></tr>
                <tr><th>€ / m²</th><td className="num">{fmtNum(item.price_per_m2)}</td></tr>
                <tr><th>Area</th><td className="num">{fmtNum(item.area_m2, ' m²')}</td></tr>
                <tr><th>Rooms</th><td className="num">{fmtNum(item.rooms)}</td></tr>
                <tr><th>Floor</th><td>{item.floor ?? '—'}</td></tr>
                <tr><th>Seller</th><td>{item.seller_name ?? '—'} {item.seller_type && `(${item.seller_type})`}</td></tr>
                <tr><th>First seen</th><td>{fmtDate(item.first_seen)}</td></tr>
                <tr><th>Last seen</th><td>{fmtDate(item.last_seen)}</td></tr>
              </tbody>
            </table>
          </div>
          <div className="stack">
            <div>
              <label>Price history {changePct !== null && (
                <span className={changePct < 0 ? 'drop-pct' : 'muted'}>
                  {changePct > 0 ? '+' : ''}{changePct.toFixed(1)}%
                </span>
              )}</label>
              <Sparkline points={item.price_history} width={320} height={70} />
              {item.price_history.length > 1 && (
                <div className="muted" style={{ fontSize: 12 }}>
                  {fmtDate(first.captured_at)} → {fmtDate(last.captured_at)},{' '}
                  {item.price_history.length} observations
                </div>
              )}
            </div>
            {item.ai && (
              <div>
                <label>AI assessment ({item.ai.model})</label>
                <div className="row" style={{ gap: 8, marginBottom: 6 }}>
                  {item.ai.verdict && <span className="badge drop">{item.ai.verdict}</span>}
                  {item.ai.score !== null && <span className="badge">score {item.ai.score}</span>}
                  {item.ai.flags?.map((f) => <span key={f} className="badge dup">{f}</span>)}
                </div>
                <div className="muted">{item.ai.reasoning}</div>
              </div>
            )}
          </div>
        </div>

        <div>
          <label>Description</label>
          {item.description
            ? <div style={{ whiteSpace: 'pre-wrap' }}>{item.description}</div>
            : <div className="muted">
                No description stored yet — it comes from the ad&rsquo;s own page,
                which is fetched separately from the search listing.
              </div>}
        </div>
      </div>

      <div className="panel">
        <div className="between" style={{ marginBottom: 10 }}>
          <label style={{ margin: 0 }}>
            Same property, other ads
            {item.duplicates.length === 0 && (
              <span className="muted"> — none linked yet</span>
            )}
          </label>
          <LinkDuplicate listingId={item.listing_id} onLinked={load} />
        </div>
        {item.duplicates.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Title</th><th>Source</th><th className="num">Price</th>
                    <th>Seller</th><th className="num">First seen</th></tr>
              </thead>
              <tbody>
                {item.duplicates.map((d) => (
                  <tr key={d.listing_id}>
                    <td><Link to={`/listing/${d.listing_id}`}>{d.title ?? '(untitled)'}</Link></td>
                    <td><span className="badge">{d.source}</span></td>
                    <td className="num">{fmtPrice(d.price_eur)}</td>
                    <td>{d.seller_name ?? '—'}</td>
                    <td className="num">{fmtDate(d.first_seen)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

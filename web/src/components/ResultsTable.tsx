import { Link } from 'react-router-dom'
import { fmtDate, fmtNum, fmtPrice } from '../api'
import type { ListingSummary, SortField } from '../types'

/** Columns that can drive server-side ordering. Clicking toggles asc/desc
 *  where both exist; sorting is done in SQL, never on the current page only. */
const SORTABLE: Partial<Record<string, [SortField, SortField]>> = {
  price: ['price_asc', 'price_desc'],
  // Ascending first: cheapest per m² is the interesting end for a buyer.
  perM2: ['price_per_m2', 'price_per_m2_desc'],
  area: ['area_asc', 'area_desc'],
  seen: ['oldest', 'newest'],
}

export function ResultsTable({ rows, sort, onSort }: {
  rows: ListingSummary[]
  sort: SortField
  onSort: (s: SortField) => void
}) {
  const header = (key: string, label: string, extraClass = '') => {
    const pair = SORTABLE[key]
    if (!pair) return <th className={extraClass}>{label}</th>
    const [asc, desc] = pair
    const active = sort === asc ? '▲' : sort === desc ? '▼' : ''
    // First click uses this column's natural direction; clicking again flips.
    const preferAsc = key === 'price' || key === 'perM2'
    const next = sort === asc ? desc : sort === desc ? asc : (preferAsc ? asc : desc)
    return (
      <th className={`sortable ${extraClass}`}
          onClick={() => onSort(next)}
          title={`Sort by ${label.toLowerCase()}`}>
        {label} {active}
      </th>
    )
  }

  if (!rows.length) {
    return (
      <div className="panel empty">
        No listings match these filters.
        <div style={{ marginTop: 8, fontSize: 13 }}>
          If the database is empty, run the crawler first:{' '}
          <code>docker compose run --rm crawler python -m crawler.run --once</code>
        </div>
      </div>
    )
  }

  return (
    <div className="panel table-wrap" style={{ padding: 0 }}>
      <table>
        <thead>
          <tr>
            <th style={{ width: 74 }}></th>
            <th>Title</th>
            <th>Location</th>
            {header('price', 'Price', 'num')}
            {header('perM2', '€/m²', 'num')}
            {header('area', 'Area', 'num')}
            <th className="num">Rooms</th>
            <th>Floor</th>
            <th>Seller</th>
            {header('seen', 'First seen', 'num')}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.listing_id}>
              <td>
                {r.thumbnail
                  ? <img className="thumb" src={r.thumbnail} alt="" loading="lazy" />
                  : <div className="thumb" />}
              </td>
              <td>
                <Link to={`/listing/${r.listing_id}`}>{r.title ?? '(untitled)'}</Link>
                <div className="row" style={{ gap: 6, marginTop: 3 }}>
                  <span className="badge">{r.source}</span>
                  {r.duplicate_count > 1 && (
                    <span className="badge dup" title="Same property listed more than once">
                      {r.duplicate_count}× duplicate
                    </span>
                  )}
                  {!r.active && <span className="badge inactive">removed</span>}
                  {r.ai_verdict === 'recommend' && (
                    <span className="badge drop">AI pick{r.ai_score ? ` ${r.ai_score}` : ''}</span>
                  )}
                </div>
              </td>
              <td>{r.location_raw ?? '—'}</td>
              <td className="num">{fmtPrice(r.price_eur)}</td>
              <td className="num">{fmtNum(r.price_per_m2)}</td>
              <td className="num">{fmtNum(r.area_m2, ' m²')}</td>
              <td className="num">{fmtNum(r.rooms)}</td>
              <td>{r.floor ?? '—'}</td>
              <td>
                {r.seller_name ?? '—'}
                {r.seller_type && <div className="muted" style={{ fontSize: 12 }}>{r.seller_type}</div>}
              </td>
              <td className="num">{fmtDate(r.first_seen)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

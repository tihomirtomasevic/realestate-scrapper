import type { PricePoint } from '../types'

/** Inline SVG price history. Points from different source ads (a repost) are
 *  marked, because that is exactly where a drop tends to hide. */
export function Sparkline({ points, width = 140, height = 32 }: {
  points: PricePoint[]
  width?: number
  height?: number
}) {
  if (points.length < 2) return <span className="muted">—</span>

  const values = points.map((p) => Number(p.price_eur))
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const pad = 3

  const coords = values.map((v, i) => {
    const x = pad + (i / (values.length - 1)) * (width - pad * 2)
    const y = height - pad - ((v - min) / span) * (height - pad * 2)
    return [x, y] as const
  })

  const path = coords.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const dropped = values[values.length - 1] < values[0]
  const stroke = dropped ? 'var(--drop)' : 'var(--muted)'

  // Where the listing_id changes, the seller reposted under a new ad.
  const repostIdx = points
    .map((p, i) => (i > 0 && p.listing_id !== points[i - 1].listing_id ? i : -1))
    .filter((i) => i > 0)

  return (
    <svg width={width} height={height} role="img"
         aria-label={`Price history, ${values.length} points, ${dropped ? 'down' : 'flat or up'}`}>
      <path d={path} fill="none" stroke={stroke} strokeWidth="1.5"
            strokeLinejoin="round" strokeLinecap="round" />
      {repostIdx.map((i) => (
        <circle key={i} cx={coords[i][0]} cy={coords[i][1]} r="2.5"
                fill="var(--warn)"><title>Reposted as a new ad here</title></circle>
      ))}
      <circle cx={coords[coords.length - 1][0]} cy={coords[coords.length - 1][1]}
              r="2" fill={stroke} />
    </svg>
  )
}

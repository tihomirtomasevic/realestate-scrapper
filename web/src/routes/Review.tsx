import { useEffect, useState } from 'react'
import { api, fmtNum, fmtPrice } from '../api'
import type { ReviewPair } from '../types'

/** Pairs the scorer was not confident about. Your labels here are the training
 *  data for retuning the dedup weights. */
export function Review() {
  const [pairs, setPairs] = useState<ReviewPair[]>([])
  const [busy, setBusy] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = () =>
    api.reviewQueue().then(setPairs).catch((e) => setError(String(e)))

  useEffect(() => { load() }, [])

  const label = async (pairId: number, value: 'same' | 'different') => {
    setBusy(pairId)
    try {
      await api.labelPair(pairId, value)
      setPairs((p) => p.filter((x) => x.pair_id !== pairId))
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="stack">
      <div className="panel">
        <strong>Duplicate review queue</strong>
        <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>
          Pairs that scored between the review and merge thresholds. Thresholds are
          deliberately biased toward splitting: a wrong merge hides a listing from
          you, a wrong split only shows it twice.
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {!error && pairs.length === 0 && (
        <div className="panel empty">Nothing to review — the queue is empty.</div>
      )}

      {pairs.map((p) => (
        <div key={p.pair_id} className="panel stack">
          <div className="between">
            <div className="row" style={{ gap: 8 }}>
              <span className="badge">score {p.score.toFixed(2)}</span>
              <span className="badge" title="Images matching within the Hamming threshold">
                {p.img_match_count} photo match{p.img_match_count === 1 ? '' : 'es'}
              </span>
              {p.text_jaccard !== null && (
                <span className="badge">text {(p.text_jaccard * 100).toFixed(0)}%</span>
              )}
              {p.phone_match && <span className="badge">same phone</span>}
              {p.floor_conflict && <span className="badge dup">floor conflict</span>}
            </div>
            <div className="row" style={{ width: 'auto' }}>
              <button className="primary" style={{ width: 'auto' }}
                      disabled={busy === p.pair_id}
                      onClick={() => label(p.pair_id, 'same')}>
                Same property
              </button>
              <button className="ghost" style={{ width: 'auto' }}
                      disabled={busy === p.pair_id}
                      onClick={() => label(p.pair_id, 'different')}>
                Different
              </button>
            </div>
          </div>

          <div className="grid-2">
            {([['A', p.listing_a, p.url_a, p.title_a, p.price_a, p.area_a],
               ['B', p.listing_b, p.url_b, p.title_b, p.price_b, p.area_b]] as const)
              .map(([side, id, url, title, price, area]) => (
                <div key={side} className="panel" style={{ background: 'var(--panel-2)' }}>
                  <div className="muted" style={{ fontSize: 12 }}>Listing {side} · #{id}</div>
                  <div style={{ margin: '4px 0' }}>{title ?? '(untitled)'}</div>
                  <div className="row" style={{ gap: 12 }}>
                    <strong>{fmtPrice(price)}</strong>
                    <span className="muted">{fmtNum(area, ' m²')}</span>
                  </div>
                  <a href={url} target="_blank" rel="noopener noreferrer">Open original ↗</a>
                </div>
              ))}
          </div>
        </div>
      ))}
    </div>
  )
}

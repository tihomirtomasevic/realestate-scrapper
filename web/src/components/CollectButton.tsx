import { useCallback, useEffect, useRef, useState } from 'react'
import { api, fmtDate } from '../api'
import type { CrawlStatus } from '../types'

/** "Collect now" trigger plus live status of the running crawl. */
export function CollectButton({ onFinished }: { onFinished?: () => void }) {
  const [status, setStatus] = useState<CrawlStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const wasActive = useRef(false)

  const refresh = useCallback(async () => {
    try {
      const s = await api.crawlStatus()
      setStatus(s)
      // Refresh the listings once a crawl finishes, so new ads appear without
      // the user reloading the page.
      if (wasActive.current && !s.active) onFinished?.()
      wasActive.current = Boolean(s.active)
    } catch {
      /* transient: the poll retries */
    }
  }, [onFinished])

  useEffect(() => {
    refresh()
    // Poll faster while a crawl is in flight, slowly when idle.
    const id = window.setInterval(refresh, status?.active ? 3000 : 20000)
    return () => window.clearInterval(id)
  }, [refresh, status?.active])

  const trigger = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.startCrawl()
      await refresh()
    } catch (e) {
      const msg = String(e)
      setError(msg.includes('409') ? 'A crawl is already running.' : msg)
    } finally {
      setBusy(false)
    }
  }

  const active = status?.active
  const last = status?.last_run

  return (
    <div className="collect">
      <button
        className="primary"
        onClick={trigger}
        disabled={busy || Boolean(active)}
        title={active ? 'A crawl is already running' : 'Fetch new ads now'}
      >
        {active
          ? `Collecting${active.status === 'pending' ? ' (queued)' : '…'}`
          : 'Collect ads now'}
      </button>

      {active && <span className="spinner" aria-hidden="true" />}

      {error && <span className="collect-msg err">{error}</span>}

      {!active && !error && last && (
        <span className="collect-msg">
          last: {fmtDate(last.finished_at)}
          {typeof last.new_ads === 'number' && ` · ${last.new_ads} new`}
          {last.error && <span className="err"> · failed</span>}
        </span>
      )}
    </div>
  )
}

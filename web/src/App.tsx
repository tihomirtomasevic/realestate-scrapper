import { useEffect, useState } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import { api, fmtDate } from './api'
import { CollectButton } from './components/CollectButton'
import { Detail } from './routes/Detail'
import { Gone } from './routes/Gone'
import { Listings } from './routes/Listings'
import { PriceDrops } from './routes/PriceDrops'
import { Review } from './routes/Review'
import type { Stats } from './types'

export default function App() {
  const [stats, setStats] = useState<Stats | null>(null)

  useEffect(() => {
    const load = () => api.stats().then(setStats).catch(() => setStats(null))
    load()
    const t = window.setInterval(load, 60_000)
    return () => window.clearInterval(t)
  }, [])

  return (
    <>
      <nav className="nav">
        <span className="brand">adcrawler</span>
        <NavLink to="/" end>Listings</NavLink>
        <NavLink to="/price-drops">
          Price drops{stats?.price_drops_30d ? ` (${stats.price_drops_30d})` : ''}
        </NavLink>
        <NavLink to="/gone">
          Off the market{stats?.listings_gone ? ` (${stats.listings_gone})` : ''}
        </NavLink>
        <NavLink to="/review">
          Review{stats?.review_queue ? ` (${stats.review_queue})` : ''}
        </NavLink>
        <span className="spacer" />
        <CollectButton onFinished={() => api.stats().then(setStats).catch(() => {})} />
        {stats && (
          <span className="stat">
            <b>{stats.listings_active.toLocaleString('hr-HR')}</b> active ·{' '}
            <b>{stats.duplicates_collapsed.toLocaleString('hr-HR')}</b> duplicates grouped ·
            last crawl {stats.last_crawl ? fmtDate(stats.last_crawl) : 'never'}
          </span>
        )}
      </nav>
      <main className="container">
        <Routes>
          <Route path="/" element={<Listings />} />
          <Route path="/price-drops" element={<PriceDrops />} />
          <Route path="/gone" element={<Gone />} />
          <Route path="/review" element={<Review />} />
          <Route path="/listing/:id" element={<Detail />} />
          <Route path="*" element={<div className="panel empty">Not found.</div>} />
        </Routes>
      </main>
    </>
  )
}

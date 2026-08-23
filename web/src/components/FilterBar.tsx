import { EMPTY_FILTERS, type Filters } from '../types'

export function FilterBar({ value, onChange, onReset, total }: {
  value: Filters
  onChange: (next: Filters) => void
  onReset: () => void
  total?: number
}) {
  const set = <K extends keyof Filters>(key: K, v: Filters[K]) =>
    onChange({ ...value, [key]: v })

  const dirty = JSON.stringify(value) !== JSON.stringify(EMPTY_FILTERS)

  return (
    <form className="panel stack" onSubmit={(e) => e.preventDefault()}>
      <div className="filters">
        <div className="wide">
          <label htmlFor="q">Search text</label>
          <input
            id="q"
            placeholder="npr. Trešnjevka, lift, novogradnja"
            value={value.q}
            onChange={(e) => set('q', e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="location">Location</label>
          <input id="location" placeholder="Trešnjevka" value={value.location}
                 onChange={(e) => set('location', e.target.value)} />
        </div>
        <div>
          <label htmlFor="min_price">Price min €</label>
          <input id="min_price" type="number" min="0" step="1000" value={value.min_price}
                 onChange={(e) => set('min_price', e.target.value)} />
        </div>
        <div>
          <label htmlFor="max_price">Price max €</label>
          <input id="max_price" type="number" min="0" step="1000" value={value.max_price}
                 onChange={(e) => set('max_price', e.target.value)} />
        </div>
        <div>
          <label htmlFor="min_area">Area min m²</label>
          <input id="min_area" type="number" min="0" value={value.min_area}
                 onChange={(e) => set('min_area', e.target.value)} />
        </div>
        <div>
          <label htmlFor="max_area">Area max m²</label>
          <input id="max_area" type="number" min="0" value={value.max_area}
                 onChange={(e) => set('max_area', e.target.value)} />
        </div>
        <div>
          <label htmlFor="rooms">Rooms</label>
          <input id="rooms" type="number" min="0" step="0.5" value={value.rooms}
                 onChange={(e) => set('rooms', e.target.value)} />
        </div>
        <div>
          <label htmlFor="seller_type">Seller</label>
          <select id="seller_type" value={value.seller_type}
                  onChange={(e) => set('seller_type', e.target.value)}>
            <option value="">Any</option>
            <option value="private">Private</option>
            <option value="agency">Agency</option>
          </select>
        </div>
      </div>

      <div className="between">
        <div className="checks">
          <label>
            <input type="checkbox" checked={value.active_only}
                   onChange={(e) => set('active_only', e.target.checked)} />
            Active only
          </label>
          <label title="Show one row per property instead of one per source ad">
            <input type="checkbox" checked={value.collapse_duplicates}
                   onChange={(e) => set('collapse_duplicates', e.target.checked)} />
            Group duplicates
          </label>
          {total !== undefined && (
            <span className="muted">{total.toLocaleString('hr-HR')} results</span>
          )}
        </div>
        {dirty && (
          <button type="button" className="ghost" style={{ width: 'auto' }} onClick={onReset}>
            Reset filters
          </button>
        )}
      </div>
    </form>
  )
}

import { useState } from 'react'
import { api } from '../api'

/** Record a duplicate the scorer cannot see.
 *
 *  Some duplicates carry no machine-detectable signal — different agencies
 *  shoot their own photos, write their own copy and state slightly different
 *  areas. A person recognises the house instantly, so let them say so.
 */
export function LinkDuplicate({ listingId, onLinked }: {
  listingId: number
  onLinked: () => void
}) {
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    // Accept a bare id or a pasted /listing/123 URL.
    const other = Number(value.trim().replace(/^.*\/listing\//, ''))
    if (!Number.isInteger(other) || other <= 0) {
      setMsg({ text: 'Enter a listing id, e.g. 155', ok: false })
      return
    }
    if (other === listingId) {
      setMsg({ text: 'That is this listing.', ok: false })
      return
    }

    setBusy(true)
    setMsg(null)
    try {
      await api.linkDuplicate(listingId, other)
      setValue('')
      setMsg({ text: `Linked with #${other}.`, ok: true })
      onLinked()
    } catch (err) {
      const s = String(err)
      setMsg({
        text: s.includes('404') ? `No listing #${other}.` : s,
        ok: false,
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="linkdup" onSubmit={submit}>
      <label htmlFor="dupof">Duplicate of</label>
      <input
        id="dupof"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="listing id"
        inputMode="numeric"
        disabled={busy}
      />
      <button type="submit" disabled={busy || !value.trim()}>
        {busy ? 'Connecting…' : 'Connect'}
      </button>
      {msg && (
        <span className={`linkdup-msg${msg.ok ? '' : ' err'}`}>{msg.text}</span>
      )}
    </form>
  )
}

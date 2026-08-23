import { useCallback, useEffect, useRef, useState } from 'react'

interface Props {
  images: { url: string; phash: string | null }[]
  alt?: string
}

/** Photo viewer: arrows, keyboard, thumbnail strip, and a full-screen lightbox. */
export function Gallery({ images, alt = '' }: Props) {
  const [index, setIndex] = useState(0)
  const [lightbox, setLightbox] = useState(false)
  const [failed, setFailed] = useState<Set<number>>(new Set())
  const stripRef = useRef<HTMLDivElement>(null)

  const count = images.length
  const go = useCallback(
    (delta: number) => setIndex((i) => (i + delta + count) % count),
    [count],
  )

  // Arrow keys page through; Escape leaves the lightbox.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') go(-1)
      else if (e.key === 'ArrowRight') go(1)
      else if (e.key === 'Escape') setLightbox(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [go])

  // Keep the active thumbnail in view when navigating with the arrows.
  useEffect(() => {
    stripRef.current
      ?.querySelector<HTMLElement>(`[data-i="${index}"]`)
      ?.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' })
  }, [index])

  if (!count) {
    return <div className="gal-empty">No photos</div>
  }

  const current = images[index]
  const broken = failed.has(index)

  const markFailed = (i: number) =>
    setFailed((prev) => new Set(prev).add(i))

  return (
    <>
      <div className="gal">
        <div className="gal-stage">
          {broken ? (
            <div className="gal-empty">Image unavailable</div>
          ) : (
            <img
              src={current.url}
              alt={alt ? `${alt} — photo ${index + 1} of ${count}` : ''}
              onClick={() => setLightbox(true)}
              onError={() => markFailed(index)}
            />
          )}

          {count > 1 && (
            <>
              <button className="gal-nav prev" onClick={() => go(-1)}
                      aria-label="Previous photo">‹</button>
              <button className="gal-nav next" onClick={() => go(1)}
                      aria-label="Next photo">›</button>
            </>
          )}
          <div className="gal-count">{index + 1} / {count}</div>
        </div>

        {count > 1 && (
          <div className="gal-strip" ref={stripRef}>
            {images.map((im, i) => (
              <button
                key={im.url}
                data-i={i}
                className={`gal-thumb${i === index ? ' active' : ''}`}
                onClick={() => setIndex(i)}
                aria-label={`Show photo ${i + 1}`}
              >
                {failed.has(i)
                  ? <span className="gal-thumb-x">×</span>
                  : <img src={im.url} alt="" loading="lazy"
                         onError={() => markFailed(i)} />}
              </button>
            ))}
          </div>
        )}
      </div>

      {lightbox && (
        <div className="gal-lightbox" onClick={() => setLightbox(false)}
             role="dialog" aria-label="Photo viewer">
          <button className="gal-close" aria-label="Close">×</button>
          {count > 1 && (
            <>
              <button className="gal-nav prev lb"
                      onClick={(e) => { e.stopPropagation(); go(-1) }}
                      aria-label="Previous photo">‹</button>
              <button className="gal-nav next lb"
                      onClick={(e) => { e.stopPropagation(); go(1) }}
                      aria-label="Next photo">›</button>
            </>
          )}
          <img src={current.url} alt={alt} onClick={(e) => e.stopPropagation()} />
          <div className="gal-count lb">{index + 1} / {count}</div>
        </div>
      )}
    </>
  )
}

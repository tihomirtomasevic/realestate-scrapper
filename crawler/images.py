"""Perceptual hashing of listing images.

This is the strongest duplicate signal available: agencies reuse the owner's
photo set across reposts and across portals, so matching photos identify a
property far more reliably than its text ever will.

Downloads go through the browser's own request context, so they carry the same
cookies and headers as the page load rather than looking like a second client.
"""
from __future__ import annotations

import io
import logging
import os
import random
import time

import imagehash
from PIL import Image

log = logging.getLogger("crawler.images")

MAX_BYTES = 8 * 1024 * 1024        # skip anything implausibly large for a photo
MIN_DIMENSION = 120                # thumbnails/icons carry no useful signal


def compute_phash(data: bytes) -> str | None:
    """64-bit perceptual hash as 16 hex chars, or None if unusable."""
    try:
        img = Image.open(io.BytesIO(data))
        # phash downsamples to 32x32 internally, so decoding a 4000px photo at
        # full resolution is pure waste. draft() makes the JPEG decoder emit a
        # reduced-size image directly — several times faster, same hash.
        if img.format == "JPEG":
            img.draft("RGB", (256, 256))
        img.load()
    except Exception as exc:
        log.debug("undecodable image (%s)", exc)
        return None

    if min(img.size) < MIN_DIMENSION:
        return None
    if img.mode != "RGB":
        img = img.convert("RGB")
    return str(imagehash.phash(img))


def backfill(conn, request_ctx, listing_ids: list[int] | None = None,
             limit: int | None = None, delay: tuple[float, float] | None = None) -> dict:
    """Hash images that do not have one yet.

    Runs after the crawl rather than inline so a slow image host never stalls
    page collection, and so it can be re-run to fill gaps.
    """
    limit = limit or int(os.getenv("PHASH_MAX_PER_RUN", "300"))
    # These are static CDN assets, not API calls — the browsing-rate delay that
    # protects the search endpoint is unnecessary here and dominated the runtime
    # (7+ minutes for 300 images). A short jitter is still polite.
    if delay is None:
        delay = (float(os.getenv("PHASH_DELAY_MIN", "0.05")),
                 float(os.getenv("PHASH_DELAY_MAX", "0.20")))
    if listing_ids:
        cur = conn.execute(
            "SELECT id, listing_id, url FROM images "
            "WHERE phash IS NULL AND listing_id = ANY(%s) ORDER BY listing_id, position "
            "LIMIT %s", (listing_ids, limit))
    else:
        cur = conn.execute(
            "SELECT id, listing_id, url FROM images "
            "WHERE phash IS NULL ORDER BY listing_id, position LIMIT %s", (limit,))
    rows = cur.fetchall()
    if not rows:
        return {"attempted": 0, "hashed": 0, "failed": 0}

    hashed = failed = 0
    started = time.time()
    # Commit in batches: a single transaction over hundreds of images means no
    # visible progress and total loss of work if the run is interrupted.
    COMMIT_EVERY = 25

    for i, row in enumerate(rows, 1):
        time.sleep(random.uniform(*delay))
        try:
            resp = request_ctx.get(row["url"], timeout=20_000)
            if not resp.ok:
                failed += 1
                continue
            body = resp.body()
            if len(body) > MAX_BYTES:
                failed += 1
                continue
            digest = compute_phash(body)
        except Exception as exc:
            log.debug("image fetch failed for %s (%s)", row["url"], exc)
            failed += 1
            continue

        if digest:
            conn.execute("UPDATE images SET phash = %s WHERE id = %s", (digest, row["id"]))
            hashed += 1
        else:
            # Mark unusable images so they are not retried on every run.
            conn.execute("UPDATE images SET phash = '' WHERE id = %s", (row["id"],))
            failed += 1

        if i % COMMIT_EVERY == 0:
            conn.commit()
            rate = i / max(time.time() - started, 0.001)
            log.info("phash: %d/%d (%.1f/s, ~%.0fs left)", i, len(rows), rate,
                     (len(rows) - i) / max(rate, 0.001))
    conn.commit()

    log.info("phash: %d hashed, %d skipped of %d in %.0fs",
             hashed, failed, len(rows), time.time() - started)
    return {"attempted": len(rows), "hashed": hashed, "failed": failed}


def refresh_blocking_keys(conn, listing_ids: list[int]) -> int:
    """Add phash band keys once hashes exist.

    Blocking keys are written at crawl time, before any image has been hashed,
    so without this pass the strongest signal never reaches candidate generation.
    """
    from .dedup import phash_bands

    cur = conn.execute(
        "SELECT listing_id, phash FROM images "
        "WHERE phash IS NOT NULL AND phash <> '' AND listing_id = ANY(%s)",
        (listing_ids,))
    added = 0
    for row in cur.fetchall():
        for band in phash_bands(row["phash"]):
            conn.execute(
                "INSERT INTO blocking_keys (listing_id, key_type, key_value) "
                "VALUES (%s, 'phash_band', %s) ON CONFLICT DO NOTHING",
                (row["listing_id"], band))
            added += 1
    conn.commit()
    return added

"""Database writes for the crawler."""
from __future__ import annotations

import hashlib
import json
import os

import psycopg
from psycopg.rows import dict_row

from . import dedup


def connect():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def content_hash(rec: dict) -> str:
    """Identity of what a crawl saw. Unchanged hash => no new observation row,
    so polling an unchanged ad daily costs nothing."""
    material = json.dumps(
        {k: rec.get(k) for k in
         ("title", "description", "price_eur", "area_m2", "rooms", "floor",
          "location_raw", "seller_name", "seller_phone", "seller_type")},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def upsert_listing(conn, source: str, source_id: str, url: str, category: str | None) -> int:
    cur = conn.execute(
        """
        INSERT INTO listings (source, source_id, url, category)
        VALUES (%(source)s, %(source_id)s, %(url)s, %(category)s)
        ON CONFLICT (source, source_id) DO UPDATE
            -- Seeing an ad in a search settles it: any pending suspicion that
            -- it had ended is withdrawn, and a genuinely re-listed ad returns.
            SET last_seen = now(), active = true, removed_at = NULL,
                status = 'active', missing_since = NULL,
                status_reason = CASE WHEN listings.status = 'gone'
                                     THEN 'relisted' ELSE NULL END,
                url = EXCLUDED.url
        RETURNING id
        """,
        {"source": source, "source_id": source_id, "url": url, "category": category},
    )
    return cur.fetchone()["id"]


def insert_observation(conn, listing_id: int, rec: dict, boilerplate: list[str]) -> int | None:
    """Append a snapshot. Returns None when nothing changed since last crawl."""
    clean = dedup.strip_boilerplate(rec.get("description") or "", boilerplate)
    fp = dedup.minhash(dedup.shingles(clean))
    cur = conn.execute(
        """
        INSERT INTO observations (
            listing_id, title, description, price_eur, area_m2, rooms, floor,
            floor_num, location_raw, location_norm, seller_type, seller_name,
            seller_phone, text_fp, raw_json, content_hash)
        VALUES (
            %(listing_id)s, %(title)s, %(description)s, %(price_eur)s, %(area_m2)s,
            %(rooms)s, %(floor)s, %(floor_num)s, %(location_raw)s, %(location_norm)s,
            %(seller_type)s, %(seller_name)s, %(seller_phone)s, %(text_fp)s,
            %(raw_json)s, %(content_hash)s)
        ON CONFLICT (listing_id, content_hash) DO NOTHING
        RETURNING id
        """,
        {
            "listing_id": listing_id,
            "title": rec.get("title"),
            "description": rec.get("description"),
            "price_eur": rec.get("price_eur"),
            "area_m2": rec.get("area_m2"),
            "rooms": rec.get("rooms"),
            "floor": rec.get("floor"),
            "floor_num": _floor_num(rec.get("floor")),
            "location_raw": rec.get("location_raw"),
            "location_norm": location_norm(rec),
            "seller_type": rec.get("seller_type"),
            "seller_name": rec.get("seller_name"),
            "seller_phone": dedup.normalize_phone(rec.get("seller_phone")),
            "text_fp": ",".join(str(x) for x in fp) if fp else None,
            "raw_json": json.dumps(rec, default=str),
            "content_hash": content_hash(rec),
        },
    )
    row = cur.fetchone()
    return row["id"] if row else None


def location_norm(rec: dict) -> str | None:
    """The narrowest place name, however the source phrased it."""
    return (dedup.location_key(rec.get("location_specific"))
            or dedup.location_key(rec.get("location_raw")))


def _floor_num(raw) -> int | None:
    val = dedup.parse_floor(raw)
    return val if isinstance(val, int) else None


def save_images(conn, listing_id: int, urls: list[str]) -> None:
    for pos, url in enumerate(urls):
        conn.execute(
            "INSERT INTO images (listing_id, url, position) VALUES (%s, %s, %s) "
            "ON CONFLICT (listing_id, url) DO NOTHING",
            (listing_id, url, pos),
        )


def save_blocking_keys(conn, listing_id: int, rec: dict, clean_text: str) -> None:
    conn.execute("DELETE FROM blocking_keys WHERE listing_id = %s", (listing_id,))
    # location_norm is derived at write time, so it is not in `rec` — without
    # passing it, blocking falls back to the raw string and keys on the country.
    payload = {**rec, "clean_text": clean_text,
               "location_norm": location_norm(rec),
               "phashes": rec.get("phashes") or []}
    for key_type, key_value in dedup.blocking_keys(payload):
        conn.execute(
            "INSERT INTO blocking_keys (listing_id, key_type, key_value) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (listing_id, key_type, key_value),
        )


def start_run(conn, source: str, label: str | None) -> int:
    cur = conn.execute(
        "INSERT INTO crawl_runs (source, search_label) VALUES (%s, %s) RETURNING id",
        (source, label))
    return cur.fetchone()["id"]


def finish_run(conn, run_id: int, **counts) -> None:
    conn.execute(
        """
        UPDATE crawl_runs SET finished_at = now(), pages = %(pages)s,
            requests = %(requests)s, new_ads = %(new_ads)s,
            updated_ads = %(updated_ads)s, error = %(error)s
        WHERE id = %(id)s
        """,
        {"id": run_id, "pages": counts.get("pages", 0),
         "requests": counts.get("requests", 0), "new_ads": counts.get("new_ads", 0),
         "updated_ads": counts.get("updated_ads", 0), "error": counts.get("error")},
    )


def mark_missing(conn, source: str, seen_ids: list[str]) -> int:
    """Ads that dropped out of every search become suspects, not casualties.

    Absence is weak evidence: promoted blocks rotate, filters wobble, pages
    fail. Flipping `active` here is what put live ads in the removed pile, so
    this only records when we stopped seeing them — crawler.gone decides.
    """
    if not seen_ids:
        return 0
    cur = conn.execute(
        """
        UPDATE listings
           SET status = 'missing',
               missing_since = COALESCE(missing_since, last_seen, now())
         WHERE source = %s AND status = 'active' AND NOT (source_id = ANY(%s))
        """,
        (source, seen_ids),
    )
    return cur.rowcount


def listing_is_new(conn, source: str, source_id: str) -> bool:
    """True when this ad has never been seen before.

    Called before upsert_listing, which would otherwise make every ad look
    pre-existing and leave crawl_runs.new_ads permanently zero.
    """
    row = conn.execute(
        "SELECT 1 FROM listings WHERE source = %s AND source_id = %s",
        (source, source_id)).fetchone()
    return row is None


def listing_id_for(conn, source: str, source_id: str) -> int | None:
    """The listing row for an ad we may or may not have seen before."""
    row = conn.execute(
        "SELECT id FROM listings WHERE source = %s AND source_id = %s",
        (source, source_id)).fetchone()
    return row["id"] if row else None


def needs_detail(conn, listing_id: int | None, rec: dict,
                 max_age_days: int = 14) -> bool:
    """Should this ad's detail page be fetched on this run?

    Fetching every ad every time is both wasteful and rude: on a 3-hour
    schedule that is >1300 page loads a day for descriptions that essentially
    never change. Fetch only when there is something to learn.

    `rec` is whatever the list phase already knows. A JSON list endpoint hands
    over price, area and title; an HTML results page may hand over nothing but
    a URL. Fields absent from `rec` are treated as unknown rather than as
    "changed to NULL" — otherwise an HTML source can never skip anything.
    """
    if listing_id is None:
        return True                                  # never seen
    row = conn.execute(
        """
        SELECT description, price_eur, area_m2, title, captured_at
        FROM observations WHERE listing_id = %s
        ORDER BY captured_at DESC LIMIT 1
        """,
        (listing_id,)).fetchone()

    if row is None:
        return True                                  # never seen
    if not row["description"]:
        return True                                  # detail never captured
    if "title" in rec and row["title"] != rec["title"]:
        return True                                  # re-listed or edited

    def changed(field) -> bool:
        if field not in rec:
            return False                             # list phase did not say
        stored, fresh = row[field], rec[field]
        if stored is None and fresh is None:
            return False
        if stored is None or fresh is None:
            return True
        return abs(float(stored) - float(fresh)) > 0.01

    if changed("price_eur"):
        return True                                  # price moved: re-read it
    if changed("area_m2"):
        return True

    # Periodic refresh so a silently edited description is eventually noticed.
    stale = conn.execute(
        "SELECT %s::timestamptz < now() - make_interval(days => %s) AS stale",
        (row["captured_at"], max_age_days)).fetchone()
    return bool(stale and stale["stale"])


def claim_crawl_request(conn):
    """Take the oldest pending request, if any.

    SKIP LOCKED keeps this safe if more than one crawler is ever running.
    """
    row = conn.execute(
        """
        UPDATE crawl_requests SET status = 'running', started_at = now()
        WHERE id = (
            SELECT id FROM crawl_requests WHERE status = 'pending'
            ORDER BY requested_at LIMIT 1 FOR UPDATE SKIP LOCKED)
        RETURNING id, source
        """).fetchone()
    conn.commit()
    return row


def finish_crawl_request(conn, request_id: int, result: dict | None, error: str | None):
    import json
    conn.execute(
        "UPDATE crawl_requests SET status = %s, finished_at = now(), "
        "result = %s, error = %s WHERE id = %s",
        ("failed" if error else "done",
         json.dumps(result) if result else None, error, request_id))
    conn.commit()

from fastapi import APIRouter, HTTPException, Query

from .. import db
from ..config import settings
from ..schemas import ListingDetail, ListingSummary, Page, SortField

router = APIRouter(prefix="/api/listings", tags=["listings"])

# Sort keys are whitelisted, never interpolated from user input.
# NULLIF(x, 0) turns a zero into a NULL so "NULLS LAST" sinks both to the
# bottom: an unpriced ad and a €0 ad are equally uninformative, and neither
# belongs at the top of a "cheapest first" list.
#
# Every ordering ends with listing_id. Without a unique tiebreaker, rows sharing
# a sort value can be returned in a different order per page, so paging past
# them silently repeats some rows and skips others.
_SORT_SQL = {
    SortField.newest: "first_seen DESC, listing_id DESC",
    SortField.oldest: "first_seen ASC, listing_id ASC",
    SortField.price_asc: "NULLIF(price_eur, 0) ASC NULLS LAST, listing_id ASC",
    SortField.price_desc: "NULLIF(price_eur, 0) DESC NULLS LAST, listing_id ASC",
    SortField.price_per_m2: "NULLIF(price_per_m2, 0) ASC NULLS LAST, listing_id ASC",
    SortField.price_per_m2_desc: "NULLIF(price_per_m2, 0) DESC NULLS LAST, listing_id ASC",
    SortField.area_desc: "NULLIF(area_m2, 0) DESC NULLS LAST, listing_id ASC",
    SortField.area_asc: "NULLIF(area_m2, 0) ASC NULLS LAST, listing_id ASC",
    SortField.relevance: "rank DESC NULLS LAST, first_seen DESC, listing_id DESC",
}

# One row per listing, joined to its most recent observation.
_BASE = """
SELECT
    l.id AS listing_id, l.cluster_id, l.source, l.url, l.active,
    l.first_seen, l.last_seen,
    o.title, o.description, o.price_eur, o.area_m2, o.rooms, o.floor,
    o.location_raw, o.seller_type, o.seller_name, o.captured_at,
    CASE WHEN o.area_m2 > 0 THEN ROUND(o.price_eur / o.area_m2, 2) END AS price_per_m2,
    COALESCE(l.cluster_id::text, 'l' || l.id) AS group_key,
    {rank} AS rank
FROM listings l
JOIN v_current o ON o.listing_id = l.id
WHERE {where}
"""


def _build(q, source, min_price, max_price, min_area, max_area, rooms,
           location, seller_type, active_only, has_price):
    where, params = ["TRUE"], {}
    rank = "0::real"

    if q:
        # plainto_tsquery handles multi-word input safely; unaccent both sides
        # so "tresnjevka" matches "Trešnjevka".
        where.append("o.search_tsv @@ plainto_tsquery('simple', immutable_unaccent(%(q)s))")
        rank = "ts_rank(o.search_tsv, plainto_tsquery('simple', immutable_unaccent(%(q)s)))"
        params["q"] = q
    if source:
        where.append("l.source = ANY(%(source)s)")
        params["source"] = source
    if min_price is not None:
        where.append("o.price_eur >= %(min_price)s")
        params["min_price"] = min_price
    if max_price is not None:
        where.append("o.price_eur <= %(max_price)s")
        params["max_price"] = max_price
    if min_area is not None:
        where.append("o.area_m2 >= %(min_area)s")
        params["min_area"] = min_area
    if max_area is not None:
        where.append("o.area_m2 <= %(max_area)s")
        params["max_area"] = max_area
    if rooms is not None:
        where.append("o.rooms = %(rooms)s")
        params["rooms"] = rooms
    if location:
        # Trigram-friendly fuzzy match on the advertised location.
        where.append("immutable_unaccent(o.location_raw) ILIKE '%%' || immutable_unaccent(%(location)s) || '%%'")
        params["location"] = location
    if seller_type:
        where.append("o.seller_type = %(seller_type)s")
        params["seller_type"] = seller_type
    if active_only:
        where.append("l.active")
    if has_price:
        where.append("o.price_eur IS NOT NULL")

    return _BASE.format(rank=rank, where=" AND ".join(where)), params


@router.get("", response_model=Page[ListingSummary])
async def search_listings(
    q: str | None = Query(None, description="Full-text search, diacritic-insensitive"),
    source: list[str] | None = Query(None),
    min_price: float | None = None,
    max_price: float | None = None,
    min_area: float | None = None,
    max_area: float | None = None,
    rooms: float | None = None,
    location: str | None = None,
    seller_type: str | None = Query(None, pattern="^(private|agency)$"),
    active_only: bool = True,
    has_price: bool = False,
    collapse_duplicates: bool = Query(
        True, description="One row per property instead of per source ad"),
    sort: SortField = SortField.newest,
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1),
):
    page_size = min(page_size or settings.api_page_size_default, settings.api_page_size_max)
    base, params = _build(q, source, min_price, max_price, min_area, max_area,
                          rooms, location, seller_type, active_only, has_price)

    if sort is SortField.relevance and not q:
        sort = SortField.newest

    # Collapse duplicates: keep the earliest-seen ad per cluster, and report how
    # many source ads that property has. Ungrouped listings key on their own id.
    picked = f"""
    WITH base AS ({base}),
    ranked AS (
        SELECT *, COUNT(*) OVER (PARTITION BY group_key) AS duplicate_count,
               ROW_NUMBER() OVER (PARTITION BY group_key
                                  ORDER BY first_seen ASC, listing_id ASC) AS rn
        FROM base
    )
    SELECT * FROM ranked WHERE {"rn = 1" if collapse_duplicates else "TRUE"}
    """

    rows = await db.fetch_all(
        f"""
        WITH picked AS ({picked})
        SELECT p.*,
               (SELECT i.url FROM images i
                 WHERE i.listing_id = p.listing_id
                 ORDER BY i.position NULLS LAST, i.id LIMIT 1) AS thumbnail,
               a.score AS ai_score, a.verdict AS ai_verdict
        FROM picked p
        LEFT JOIN LATERAL (
            SELECT aa.score, aa.verdict FROM ai_analysis aa
            JOIN observations oo ON oo.id = aa.observation_id
            WHERE oo.listing_id = p.listing_id
            ORDER BY aa.created_at DESC LIMIT 1
        ) a ON TRUE
        ORDER BY {_SORT_SQL[sort]}
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    )
    total_row = await db.fetch_one(
        f"WITH picked AS ({picked}) SELECT COUNT(*) AS n FROM picked", params)
    total = total_row["n"] if total_row else 0

    return Page[ListingSummary](
        items=[ListingSummary(**r) for r in rows],
        total=total, page=page, page_size=page_size,
        pages=(total + page_size - 1) // page_size,
    )


@router.get("/{listing_id}", response_model=ListingDetail)
async def get_listing(listing_id: int):
    row = await db.fetch_one(
        """
        SELECT l.id AS listing_id, l.cluster_id, l.source, l.url, l.active,
               l.first_seen, l.last_seen,
               o.title, o.description, o.price_eur, o.area_m2, o.rooms, o.floor,
               o.location_raw, o.seller_type, o.seller_name, o.seller_phone,
               o.lat, o.lon, o.captured_at,
               CASE WHEN o.area_m2 > 0 THEN ROUND(o.price_eur / o.area_m2, 2) END AS price_per_m2
        FROM listings l JOIN v_current o ON o.listing_id = l.id
        WHERE l.id = %(id)s
        """,
        {"id": listing_id},
    )
    if not row:
        raise HTTPException(404, "listing not found")

    # Price history spans the whole cluster, so a repost's drop is visible.
    history = await db.fetch_all(
        """
        SELECT o.captured_at, o.price_eur, o.listing_id
        FROM observations o
        JOIN listings l ON l.id = o.listing_id
        WHERE o.price_eur IS NOT NULL
          -- Casts are required: Postgres cannot infer a bare parameter's type
          -- from `$n IS NOT NULL` alone (AmbiguousParameter).
          AND (l.id = %(id)s
               OR (%(cluster)s::bigint IS NOT NULL AND l.cluster_id = %(cluster)s::bigint))
        ORDER BY o.captured_at
        """,
        {"id": listing_id, "cluster": row["cluster_id"]},
    )
    images = await db.fetch_all(
        "SELECT url, phash, position FROM images WHERE listing_id = %(id)s "
        "ORDER BY position NULLS LAST, id",
        {"id": listing_id},
    )
    ai = await db.fetch_one(
        """
        SELECT aa.model, aa.score, aa.verdict, aa.reasoning,
               aa.flags_json AS flags, aa.created_at
        FROM ai_analysis aa
        JOIN observations o ON o.id = aa.observation_id
        WHERE o.listing_id = %(id)s
        ORDER BY aa.created_at DESC LIMIT 1
        """,
        {"id": listing_id},
    )
    dupes = await db.fetch_all(
        """
        SELECT l.id AS listing_id, l.cluster_id, l.source, l.url, l.active,
               l.first_seen, l.last_seen,
               o.title, o.price_eur, o.area_m2, o.rooms, o.floor, o.location_raw,
               o.seller_type, o.seller_name, o.captured_at,
               CASE WHEN o.area_m2 > 0 THEN ROUND(o.price_eur / o.area_m2, 2) END AS price_per_m2
        FROM listings l JOIN v_current o ON o.listing_id = l.id
        WHERE l.cluster_id = %(cluster)s AND l.id <> %(id)s
        """,
        {"cluster": row["cluster_id"], "id": listing_id},
    ) if row["cluster_id"] else []

    return ListingDetail(
        **row,
        duplicate_count=len(dupes) + 1,
        thumbnail=images[0]["url"] if images else None,
        ai_score=ai["score"] if ai else None,
        ai_verdict=ai["verdict"] if ai else None,
        price_history=history, images=images, ai=ai,
        duplicates=[ListingSummary(**d) for d in dupes],
    )

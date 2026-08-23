"""Ads that stopped running.

The most informative listings are the ones that ended: whatever was wrong with
the rest is still on the market. What the portals will not tell us is *why* an
ad ended — neither marks real estate as sold — so this feed reports the ending
and the evidence around it (how long it ran, what the price did, how it
compared locally) and leaves the conclusion to the reader.
"""
from fastapi import APIRouter, Query

from .. import db
from ..config import settings
from ..schemas import GoneListing, GoneSort, Page

router = APIRouter(prefix="/api/gone", tags=["gone"])

# Whitelisted orderings; every one ends on listing_id so paging is stable.
_SORT_SQL = {
    GoneSort.recent: "COALESCE(removed_at, last_seen) DESC, listing_id DESC",
    GoneSort.fastest: "days_listed ASC NULLS LAST, listing_id ASC",
    GoneSort.slowest: "days_listed DESC NULLS LAST, listing_id ASC",
    GoneSort.best_value: "vs_area_median_pct ASC NULLS LAST, listing_id ASC",
    GoneSort.biggest_drop: "price_change_pct ASC NULLS LAST, listing_id ASC",
    GoneSort.price_asc: "NULLIF(price_eur, 0) ASC NULLS LAST, listing_id ASC",
    GoneSort.price_desc: "NULLIF(price_eur, 0) DESC NULLS LAST, listing_id ASC",
}


@router.get("", response_model=Page[GoneListing])
async def gone_listings(
    source: list[str] | None = Query(None),
    reason: str | None = Query(None, pattern="^(expired|deleted|not_listed)$"),
    days: int = Query(365, ge=1, description="Ended within this many days"),
    min_price: float | None = None,
    max_price: float | None = None,
    max_days_listed: float | None = Query(
        None, description="Only ads that ended within this long of appearing"),
    hide_still_listed: bool = Query(
        True, description="Hide ads whose property is still advertised elsewhere"),
    sort: GoneSort = GoneSort.recent,
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1),
):
    page_size = min(page_size or settings.api_page_size_default,
                    settings.api_page_size_max)
    where, params = ["COALESCE(removed_at, last_seen) >= now() - make_interval(days => %(days)s)"], {"days": days}

    if source:
        where.append("source = ANY(%(source)s)")
        params["source"] = source
    if reason:
        where.append("status_reason = %(reason)s")
        params["reason"] = reason
    # Every numeric parameter carries an explicit cast: Postgres cannot infer a
    # bare parameter's type in these comparisons, and raises AmbiguousParameter.
    if min_price is not None:
        where.append("price_eur >= %(min_price)s::numeric")
        params["min_price"] = min_price
    if max_price is not None:
        where.append("price_eur <= %(max_price)s::numeric")
        params["max_price"] = max_price
    if max_days_listed is not None:
        where.append("days_listed <= %(max_days)s::numeric")
        params["max_days"] = max_days_listed
    if hide_still_listed:
        where.append("NOT still_listed_elsewhere")

    base = f"SELECT * FROM v_gone_listings WHERE {' AND '.join(where)}"

    rows = await db.fetch_all(
        f"""
        WITH g AS ({base})
        SELECT g.*,
               (SELECT i.url FROM images i
                 WHERE i.listing_id = g.listing_id
                 ORDER BY i.position NULLS LAST, i.id LIMIT 1) AS thumbnail
        FROM g
        ORDER BY {_SORT_SQL[sort]}
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    )
    total_row = await db.fetch_one(
        f"WITH g AS ({base}) SELECT COUNT(*) AS n FROM g", params)
    total = total_row["n"] if total_row else 0

    return Page[GoneListing](
        items=[GoneListing(**r) for r in rows],
        total=total, page=page, page_size=page_size,
        pages=(total + page_size - 1) // page_size,
    )

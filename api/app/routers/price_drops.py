"""Price-drop feed.

Reads CLUSTER-level history, which is the whole point: a seller who deletes an
ad and reposts it cheaper creates a new source ad id, so the drop does not exist
at listing level. Only the cluster sees it.
"""
from fastapi import APIRouter, Query

from .. import db
from ..schemas import PriceDrop

router = APIRouter(prefix="/api/price-drops", tags=["price-drops"])


@router.get("", response_model=list[PriceDrop])
async def price_drops(
    min_drop_pct: float = Query(2.0, ge=0, description="Minimum drop, percent"),
    days: int = Query(90, ge=1, description="Only clusters seen within this window"),
    min_price: float | None = None,
    max_price: float | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    rows = await db.fetch_all(
        """
        WITH drops AS (
            SELECT * FROM v_cluster_price_changes
            WHERE change_pct <= -%(min_drop)s
              AND last_seen >= now() - make_interval(days => %(days)s)
              -- Casts are required: Postgres cannot infer a bare parameter's
              -- type from `$n IS NULL` alone (AmbiguousParameter).
              AND (%(min_price)s::numeric IS NULL OR latest_price >= %(min_price)s::numeric)
              AND (%(max_price)s::numeric IS NULL OR latest_price <= %(max_price)s::numeric)
            ORDER BY change_pct ASC
            LIMIT %(limit)s
        )
        SELECT d.cluster_id, d.first_price, d.latest_price, d.change_pct,
               d.latest_price - d.first_price AS change_eur,
               d.listing_count, d.first_seen, d.last_seen,
               rep.listing_id, rep.source, rep.url, rep.title,
               rep.location_raw, rep.area_m2,
               COALESCE(hist.points, '[]'::json) AS price_history
        FROM drops d
        -- Representative ad: the most recently seen active one in the cluster.
        JOIN LATERAL (
            SELECT l.id AS listing_id, l.source, l.url,
                   o.title, o.location_raw, o.area_m2
            FROM listings l JOIN v_current o ON o.listing_id = l.id
            WHERE l.cluster_id = d.cluster_id
            ORDER BY l.active DESC, l.last_seen DESC
            LIMIT 1
        ) rep ON TRUE
        LEFT JOIN LATERAL (
            SELECT json_agg(json_build_object(
                       'captured_at', h.captured_at,
                       'price_eur', h.price_eur,
                       'listing_id', h.listing_id)
                   ORDER BY h.captured_at) AS points
            FROM v_cluster_price_history h
            WHERE h.cluster_id = d.cluster_id
        ) hist ON TRUE
        ORDER BY d.change_pct ASC
        """,
        {"min_drop": min_drop_pct, "days": days, "limit": limit,
         "min_price": min_price, "max_price": max_price},
    )
    return [PriceDrop(**r) for r in rows]

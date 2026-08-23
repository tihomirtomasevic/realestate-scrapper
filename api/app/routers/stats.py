from fastapi import APIRouter

from .. import db
from ..schemas import Stats

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("", response_model=Stats)
async def stats():
    row = await db.fetch_one(
        """
        SELECT
            (SELECT COUNT(*) FROM listings)                       AS listings_total,
            (SELECT COUNT(*) FROM listings WHERE active)          AS listings_active,
            (SELECT COUNT(*) FROM clusters)                       AS clusters_total,
            -- source ads hidden behind a collapsed property row
            (SELECT COALESCE(SUM(cnt - 1), 0) FROM (
                SELECT COUNT(*) AS cnt FROM listings
                WHERE cluster_id IS NOT NULL GROUP BY cluster_id) s) AS duplicates_collapsed,
            (SELECT COUNT(*) FROM observations)                   AS observations_total,
            (SELECT COUNT(*) FROM v_cluster_price_changes
              WHERE change_pct < 0
                AND last_seen >= now() - interval '30 days')      AS price_drops_30d,
            (SELECT COUNT(*) FROM listings WHERE status = 'gone')  AS listings_gone,
            (SELECT COUNT(*) FROM listings WHERE status = 'missing') AS listings_missing,
            (SELECT COUNT(*) FROM listings WHERE status = 'out_of_scope')
                                                                   AS listings_out_of_scope,
            (SELECT COUNT(*) FROM dedup_pairs
              WHERE decision = 'review' AND human_label IS NULL)  AS review_queue,
            (SELECT MAX(finished_at) FROM crawl_runs)             AS last_crawl,
            (SELECT COALESCE(ARRAY_AGG(DISTINCT source), '{}') FROM listings) AS sources
        """
    )
    return Stats(**row)

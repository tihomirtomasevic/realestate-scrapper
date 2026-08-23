"""On-demand crawl triggering.

The API cannot start the crawler container directly — doing that would mean
handing it the Docker socket, which is effectively root on the host. Instead a
request row is queued here and the crawler, which already runs on a schedule,
picks it up on its next poll.
"""
from fastapi import APIRouter, HTTPException, Query
from psycopg import errors

from .. import db
from ..schemas import CrawlRequestOut, CrawlStatus

router = APIRouter(prefix="/api/crawl", tags=["crawl"])


@router.post("", response_model=CrawlRequestOut, status_code=202)
async def request_crawl(source: str | None = Query(None, description="Omit for all sources")):
    """Queue a crawl. Returns 409 if one is already queued or running."""
    try:
        row = await db.fetch_one(
            "INSERT INTO crawl_requests (source, requested_by) "
            "VALUES (%(source)s, 'ui') RETURNING *",
            {"source": source},
        )
    except errors.UniqueViolation:
        raise HTTPException(409, "a crawl is already queued or running")
    except Exception as exc:
        if "idx_crawl_requests_one_active" in str(exc):
            raise HTTPException(409, "a crawl is already queued or running")
        raise
    return CrawlRequestOut(**row)


@router.get("/status", response_model=CrawlStatus)
async def crawl_status():
    active = await db.fetch_one(
        "SELECT * FROM crawl_requests WHERE status IN ('pending','running') "
        "ORDER BY requested_at LIMIT 1")
    recent = await db.fetch_all(
        "SELECT * FROM crawl_requests ORDER BY requested_at DESC LIMIT 5")
    last_run = await db.fetch_one(
        """
        SELECT source, search_label, started_at, finished_at, pages, requests,
               new_ads, updated_ads, error
        FROM crawl_runs WHERE finished_at IS NOT NULL
        ORDER BY finished_at DESC LIMIT 1
        """)
    return CrawlStatus(
        active=CrawlRequestOut(**active) if active else None,
        recent=[CrawlRequestOut(**r) for r in recent],
        last_run=last_run,
    )


@router.post("/cancel", response_model=CrawlRequestOut | None)
async def cancel_pending():
    """Drop a queued request. A crawl already running is left alone — killing it
    mid-flight would leave partial data behind."""
    row = await db.fetch_one(
        "UPDATE crawl_requests SET status = 'cancelled', finished_at = now() "
        "WHERE status = 'pending' RETURNING *")
    return CrawlRequestOut(**row) if row else None

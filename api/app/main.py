import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .config import settings
from .routers import crawl, dedup, gone, listings, price_drops, stats

logging.basicConfig(level=settings.log_level)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.open_pool()
    yield
    await db.close_pool()


app = FastAPI(
    title="adcrawler API",
    version="0.1.0",
    description=(
        "Search and price-history API over locally crawled classified ads. "
        "Duplicate source ads are grouped into clusters, so price history "
        "survives a seller deleting and reposting an ad."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(listings.router)
app.include_router(price_drops.router)
app.include_router(gone.router)
app.include_router(dedup.router)
app.include_router(stats.router)
app.include_router(crawl.router)


@app.get("/api/health", tags=["meta"])
async def health():
    try:
        await db.fetch_one("SELECT 1 AS ok")
        return {"status": "ok", "database": "up"}
    except Exception as exc:  # surfaced to the compose healthcheck
        return {"status": "degraded", "database": "down", "error": str(exc)}

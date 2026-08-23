from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class SortField(str, Enum):
    newest = "newest"
    oldest = "oldest"
    price_asc = "price_asc"
    price_desc = "price_desc"
    price_per_m2 = "price_per_m2"            # ascending: best value first
    price_per_m2_desc = "price_per_m2_desc"
    area_desc = "area_desc"
    area_asc = "area_asc"
    relevance = "relevance"   # only meaningful together with ?q=


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


class ListingSummary(BaseModel):
    listing_id: int
    cluster_id: int | None
    source: str
    url: str
    title: str | None
    price_eur: Decimal | None
    area_m2: Decimal | None
    price_per_m2: Decimal | None
    rooms: Decimal | None
    floor: str | None
    location_raw: str | None
    seller_type: str | None
    seller_name: str | None
    active: bool
    first_seen: datetime
    last_seen: datetime
    captured_at: datetime
    # How many source ads are in this property's cluster: >1 means the same
    # place is listed more than once (repost, or several agencies).
    duplicate_count: int = 1
    thumbnail: str | None = None
    ai_score: float | None = None
    ai_verdict: str | None = None


class PricePoint(BaseModel):
    captured_at: datetime
    price_eur: Decimal
    listing_id: int


class ImageOut(BaseModel):
    url: str
    phash: str | None
    position: int | None


class AiAnalysisOut(BaseModel):
    model: str
    score: float | None
    verdict: str | None
    reasoning: str | None
    flags: list[str] | None
    created_at: datetime


class ListingDetail(ListingSummary):
    description: str | None
    lat: float | None
    lon: float | None
    seller_phone: str | None
    price_history: list[PricePoint]
    images: list[ImageOut]
    ai: AiAnalysisOut | None
    # Other source ads for the same property.
    duplicates: list[ListingSummary]


class PriceDrop(BaseModel):
    cluster_id: int
    listing_id: int
    source: str
    url: str
    title: str | None
    location_raw: str | None
    area_m2: Decimal | None
    first_price: Decimal
    latest_price: Decimal
    change_pct: float
    change_eur: Decimal
    listing_count: int
    first_seen: datetime
    last_seen: datetime
    price_history: list[PricePoint]


class GoneSort(str, Enum):
    """Orderings for the ended-ads feed."""
    recent = "recent"                # ended most recently
    fastest = "fastest"              # shortest time on the market
    slowest = "slowest"
    best_value = "best_value"        # cheapest per m2 against its own area
    biggest_drop = "biggest_drop"    # cut its price the most before ending
    price_asc = "price_asc"
    price_desc = "price_desc"


class GoneListing(BaseModel):
    """An ad that stopped running.

    `reason` is what the site told us — expired, deleted, or simply no longer
    listed. Neither portal says whether a house sold, so nothing here claims
    it did; `days_listed`, `price_change_pct` and `vs_area_median_pct` are the
    evidence for reading it that way.
    """
    listing_id: int
    cluster_id: int | None
    source: str
    url: str
    title: str | None
    location_raw: str | None
    price_eur: Decimal | None
    area_m2: Decimal | None
    rooms: Decimal | None
    price_per_m2: Decimal | None
    seller_type: str | None
    seller_name: str | None
    status_reason: str | None
    first_seen: datetime
    last_seen: datetime
    removed_at: datetime | None
    days_listed: Decimal | None
    first_price: Decimal | None
    last_price: Decimal | None
    price_change_pct: float | None
    median_price_per_m2: Decimal | None
    vs_area_median_pct: float | None
    still_listed_elsewhere: bool
    thumbnail: str | None = None


class ReviewPair(BaseModel):
    pair_id: int
    score: float
    listing_a: int
    listing_b: int
    url_a: str
    url_b: str
    title_a: str | None
    title_b: str | None
    price_a: Decimal | None
    price_b: Decimal | None
    area_a: Decimal | None
    area_b: Decimal | None
    img_match_count: int
    text_jaccard: float | None
    floor_conflict: bool
    phone_match: bool
    evidence: dict | None


class LinkRequest(BaseModel):
    listing_a: int
    listing_b: int
    label: str = "same"      # 'same' | 'different'


class ReviewLabel(BaseModel):
    label: str   # 'same' | 'different'


class Stats(BaseModel):
    listings_total: int
    listings_active: int
    clusters_total: int
    duplicates_collapsed: int
    observations_total: int
    price_drops_30d: int
    # Confirmed ended, versus merely absent from the last search and not yet
    # verified — the two are very different claims.
    listings_gone: int
    listings_missing: int
    # Live ads our search no longer returns — promoted placements, mostly.
    listings_out_of_scope: int
    review_queue: int
    last_crawl: datetime | None
    sources: list[str]


class CrawlRequestOut(BaseModel):
    id: int
    source: str | None
    requested_by: str
    status: str
    requested_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict | None = None
    error: str | None = None


class CrawlStatus(BaseModel):
    active: CrawlRequestOut | None
    recent: list[CrawlRequestOut]
    # Last finished crawl_runs row, for "when did this last actually collect".
    last_run: dict | None

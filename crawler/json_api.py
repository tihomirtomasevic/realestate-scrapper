"""List-phase adapter for sites that render results from a JSON endpoint.

Calling the API directly beats parsing HTML: JSON field names change far less
often than CSS classes, and the payload is already typed. Everything about the
endpoint — URL, params, field paths, URL templates — comes from the (gitignored)
source config, so no site knowledge lives in this file.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("crawler.json_api")


@dataclass(frozen=True)
class ApiConfig:
    endpoint: str
    params: dict[str, Any] = field(default_factory=dict)
    session_url: str | None = None      # visit once if the API needs a cookie
    page_param: str = "page"
    start_page: int = 1
    records_path: str = "data"
    next_page_path: str | None = None
    stop_when_next_page: Any = -1
    total_path: str | None = None
    fields: dict[str, str] = field(default_factory=dict)
    image_url_template: str | None = None
    detail_url_template: str | None = None
    fetch_detail: bool = False          # also load the HTML page per ad?
    # Agencies routinely list "price on request" as a token 1 or 100. Treating
    # those as real prices ruins sorting and would fire a fake price-drop alert
    # the moment a genuine figure replaces them, so anything below this becomes
    # NULL ("na upit") with a flag kept in raw_json.
    min_plausible_price: float = 0.0
    # Only the first N images are needed to identify a property; hashing 25
    # photos per ad costs minutes and buys nothing.
    max_images: int | None = None

    @staticmethod
    def parse(raw: dict | None, where: str):
        if not raw:
            return None
        if not raw.get("endpoint"):
            raise ValueError(f"{where}.api.endpoint is required")
        if not raw.get("fields", {}).get("source_id"):
            raise ValueError(
                f"{where}.api.fields.source_id is required — without a stable id "
                f"every crawl would look like a brand-new ad")
        return ApiConfig(
            endpoint=raw["endpoint"],
            params=raw.get("params") or {},
            session_url=raw.get("session_url"),
            page_param=raw.get("page_param", "page"),
            start_page=int(raw.get("start_page", 1)),
            records_path=raw.get("records_path", "data"),
            next_page_path=raw.get("next_page_path"),
            stop_when_next_page=raw.get("stop_when_next_page", -1),
            total_path=raw.get("total_path", "count"),
            fields=raw.get("fields") or {},
            image_url_template=raw.get("image_url_template"),
            detail_url_template=raw.get("detail_url_template"),
            fetch_detail=bool(raw.get("fetch_detail", False)),
            min_plausible_price=float(raw.get("min_plausible_price", 0) or 0),
            max_images=raw.get("max_images"),
        )


def dig(obj, path: str):
    """Resolve a dotted path: 'summary.area' -> obj['summary']['area']."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)] if int(part) < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur


def build_url(api: ApiConfig, page: int) -> str:
    from urllib.parse import urlencode

    params = dict(api.params)
    params[api.page_param] = page + api.start_page - 1
    # Booleans must serialise as JSON literals, not Python's True/False.
    flat = {k: ("true" if v is True else "false" if v is False else v)
            for k, v in params.items()}
    return f"{api.endpoint}?{urlencode(flat)}"


def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def map_record(api: ApiConfig, raw: dict) -> dict:
    """Turn one API record into the shape the storage layer expects."""
    f = api.fields
    get = lambda name: dig(raw, f[name]) if name in f else None  # noqa: E731

    source_id = get("source_id")

    # Location may arrive as a hierarchy list, e.g.
    # ["COUNTRY", "Region", "Municipality", "Village"].
    loc = get("location_parts")
    if isinstance(loc, list) and loc:
        location_raw = ", ".join(str(p) for p in loc if p)
        location_specific = str(loc[-1])
    else:
        location_raw = get("location_raw") or (str(loc) if loc else None)
        location_specific = location_raw

    # A company flag maps onto the seller_type vocabulary the scorer uses:
    # an agency phone is not identifying, a private one is.
    seller_type = None
    if "seller_is_company" in f:
        flag = get("seller_is_company")
        if flag is not None:
            seller_type = "agency" if flag else "private"
    elif "seller_type" in f:
        seller_type = get("seller_type")

    images = get("images") or []
    if not isinstance(images, list):
        images = []
    if api.max_images:
        images = images[:api.max_images]
    if api.image_url_template:
        images = [api.image_url_template.replace("{value}", str(i)) for i in images]

    url = None
    if api.detail_url_template:
        url = api.detail_url_template
        for name in f:
            token = "{" + name + "}"
            if token in url:
                url = url.replace(token, str(get(name) or ""))

    price = _num(get("price_eur"))
    price_placeholder = (
        price is not None and api.min_plausible_price > 0
        and price < api.min_plausible_price)
    if price_placeholder:
        price = None            # NULL means "na upit", which the schema expects

    return {
        "source_id": str(source_id),
        "price_placeholder": price_placeholder,
        "url": url,
        "title": get("title"),
        "description": get("description"),
        "price_eur": price,
        "area_m2": _num(get("area_m2")),
        "rooms": _num(get("rooms")),
        "floor": get("floor"),
        "location_raw": location_raw,
        "location_specific": location_specific,
        "seller_type": seller_type,
        "seller_name": get("seller_name"),
        "seller_phone": get("seller_phone"),
        "images": images,
        # Extras the API volunteers: kept in raw_json for later use.
        "posted_at": get("posted_at"),
        "renewed_at": get("renewed_at"),
        "previous_price": _num(get("previous_price")),
        "reduction_pct": _num(get("reduction_pct")),
        "price_per_m2": _num(get("price_per_m2")),
        "year_built": get("year_built"),
    }


def fetch_page(request_ctx, api: ApiConfig, page: int, timeout: int = 30_000):
    """Return (records, next_page, total). next_page is None when finished."""
    url = build_url(api, page)
    resp = request_ctx.get(url, timeout=timeout)
    if resp.status >= 400:
        raise RuntimeError(
            f"{resp.status} from {api.endpoint} — if this is 400/403 the endpoint "
            f"probably needs a session; set api.session_url. Body: {resp.text()[:160]}")

    payload = resp.json()
    rows = dig(payload, api.records_path) if api.records_path else payload
    if not isinstance(rows, list):
        raise RuntimeError(
            f"records_path {api.records_path!r} did not resolve to a list "
            f"(got {type(rows).__name__}); check the envelope shape")

    total = dig(payload, api.total_path) if api.total_path else None

    nxt = None
    if api.next_page_path:
        val = dig(payload, api.next_page_path)
        if val is not None and val != api.stop_when_next_page:
            nxt = page + 1
    elif rows:
        nxt = page + 1          # no cursor field: stop when a page comes back empty

    return rows, nxt, total

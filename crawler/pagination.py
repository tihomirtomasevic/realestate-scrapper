"""Building the URL for page N of a search.

Deliberately free of any Playwright import so it can be unit-tested without a
browser — this is fiddly, site-specific string handling and it needs tests.
"""
from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse, urlunparse

log = logging.getLogger("crawler.pagination")


def page_url(cfg, search_url: str, page: int) -> str:
    if page <= 1 or not cfg.pagination:
        return search_url
    mode = cfg.pagination.get("mode", "query_param")
    n = page + int(cfg.pagination.get("start_at", 1)) - 1

    if mode == "query_param":
        parts = urlparse(search_url)
        query = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        query[cfg.pagination.get("param", "page")] = str(n)
        return urlunparse(parts._replace(query=urlencode(query)))

    if mode == "path_suffix":
        return f"{search_url.rstrip('/')}/{n}"

    if mode == "json_param":
        return _json_param(cfg, search_url, n)

    return search_url  # next_link follows an anchor on the page instead


def _json_param(cfg, search_url: str, n: int) -> str:
    """Page number lives inside a URL parameter holding JSON.

    Some search UIs pack the entire query into one parameter — and may
    URL-encode it twice, so a naive re-encode corrupts every other filter.
    Decode to the configured depth, bump the page key, re-encode identically.
    """
    param = cfg.pagination.get("param", "searchQuery")
    page_key = cfg.pagination.get("json_page_key", "page")
    depth = 2 if cfg.pagination.get("double_encoded") else 1

    parts = urlparse(search_url)
    query = parse_qs(parts.query, keep_blank_values=True)
    if param not in query:
        log.warning("pagination.param %r is not in the search URL", param)
        return search_url

    raw = query[param][0]
    for _ in range(depth):
        raw = unquote(raw)
    try:
        blob = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("pagination.param %r does not hold JSON at depth %d", param, depth)
        return search_url

    blob[page_key] = n
    encoded = json.dumps(blob, separators=(",", ":"))
    for _ in range(depth):
        encoded = quote(encoded, safe="")

    # Rebuild by hand: urlencode would re-escape the blob we just encoded.
    flat = {k: v[0] for k, v in query.items()}
    flat[param] = encoded
    return urlunparse(parts._replace(
        query="&".join(f"{k}={v}" for k, v in flat.items())))

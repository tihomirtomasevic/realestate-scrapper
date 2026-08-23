"""Confirming that an ad has really stopped running.

The listing phase can only observe absence, and absence lies. Promoted blocks
rotate their contents, a search filter can wobble, a page can fail to render.
On 2026-08-22 every listing this project had flagged inactive turned out to
still be live: they were paid placements that scrolled off page one.

So a missing ad is a question, not an answer. This module goes back to the ad
itself and reads what the site says about it, and only then records an ending.

Neither portal marks real estate as sold. An ad that sold and an ad the seller
withdrew look exactly the same from outside, so nothing here claims to know
which happened — it records that the ad ended, when, and at what price, and
leaves the reading of it to whoever looks at the feed.
"""
from __future__ import annotations

import logging
import re

from selectolax.parser import HTMLParser

log = logging.getLogger("crawler.gone")

# Outcomes of a single check.
ALIVE, EXPIRED, DELETED, ABSENT, UNKNOWN = (
    "alive", "expired", "deleted", "absent", "unknown")


def _dig(payload, path: str):
    """Follow a dotted path into a JSON payload."""
    node = payload
    for part in path.split("."):
        if isinstance(node, list):
            node = node[int(part)] if part.isdigit() and len(node) > int(part) else None
        elif isinstance(node, dict):
            node = node.get(part)
        else:
            return None
    return node


def _visible_text(html: str) -> str:
    """Page text with scripts and styles removed.

    Matching raw HTML would hit the configured markers inside inline scripts:
    sites commonly ship the "no longer running" wording in a client-side
    template on every page, live or not, so a substring search over the source
    reports that every ad has ended.
    """
    tree = HTMLParser(html)
    for node in tree.css("script, style, noscript"):
        node.decompose()
    return re.sub(r"\s+", " ", tree.text(separator=" "))


def check_via_api(request, spec, source_id: str) -> tuple[str, str]:
    """Ask the site's own endpoint whether it still offers this ad."""
    url = spec.api_probe_url.replace("{source_id}", str(source_id))
    resp = request.get(url)
    if resp.status in spec.deleted_on_status:
        return DELETED, f"api http {resp.status}"
    if resp.status >= 400:
        return UNKNOWN, f"api http {resp.status}"
    try:
        payload = resp.json()
    except Exception:
        return UNKNOWN, "api response was not json"
    count = _dig(payload, spec.api_count_path) if spec.api_count_path else None
    if count is None:
        return UNKNOWN, f"no count at {spec.api_count_path!r}"
    return (ALIVE, "api still lists it") if int(count) > 0 else (
        ABSENT, "api no longer lists it")


def check_via_page(page, spec, url: str) -> tuple[str, str]:
    """Read the ad's own page and look for the site's end-of-ad marker."""
    resp = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    status = resp.status if resp else None
    # The status code is read before the markup on purpose: a withdrawn ad
    # serves 410 AND the same "no longer running" notice an expired one does,
    # so checking the page first would file every takedown as an expiry.
    if status in spec.deleted_on_status:
        return DELETED, f"page http {status}"
    if status and status >= 400:
        return UNKNOWN, f"page http {status}"

    html = page.content()
    if spec.expired_selector and HTMLParser(html).css_first(spec.expired_selector):
        return EXPIRED, f"page shows {spec.expired_selector}"
    if spec.expired_text:
        text = _visible_text(html)
        for marker in spec.expired_text:
            if marker.lower() in text.lower():
                return EXPIRED, f"page says {marker!r}"
    if spec.alive_selector and HTMLParser(html).css_first(spec.alive_selector):
        return ALIVE, "page still renders the ad"
    return UNKNOWN, "page gave no verdict"


def verify(conn, cfg, page) -> dict:
    """Re-check every ad that went missing, and settle its status."""
    spec = cfg.gone
    if spec is None:
        log.info("%s: no gone: block configured — missing ads stay unconfirmed",
                 cfg.key)
        return {"checked": 0}

    rows = conn.execute(
        """
        SELECT id, source_id, url FROM listings
        WHERE source = %s AND status = 'missing'
          -- make_interval(hours => ...) wants an integer; multiplying an
          -- interval keeps fractional hours usable for a tighter recheck.
          AND (status_checked_at IS NULL
               OR status_checked_at < now() - (%s * interval '1 hour'))
        ORDER BY missing_since NULLS FIRST
        LIMIT %s
        """,
        (cfg.key, spec.recheck_after_hours, spec.max_checks_per_run),
    ).fetchall()
    if not rows:
        return {"checked": 0}

    log.info("%s: re-checking %d missing ad(s)", cfg.key, len(rows))
    tally = {"checked": 0, "gone": 0, "alive": 0, "unknown": 0}

    for row in rows:
        try:
            if spec.api_probe_url:
                verdict, why = check_via_api(page.context.request, spec,
                                             row["source_id"])
            else:
                verdict, why = check_via_page(page, spec, row["url"])
        except Exception as exc:                 # a bad page must not end the pass
            verdict, why = UNKNOWN, f"check failed: {exc}"
        tally["checked"] += 1

        if verdict == ALIVE:
            # Live, but our search no longer returns it — so it is neither an
            # ending to report nor a result to show. This is where promoted
            # placements end up: they were never ours to track. Terminal on
            # purpose, so we stop paying for the same answer every few hours;
            # if a real search ever returns the ad again, upsert_listing puts
            # it straight back to active.
            conn.execute(
                """
                UPDATE listings
                   SET status = 'out_of_scope', status_reason = 'still_running',
                       active = false, removed_at = NULL,
                       status_checked_at = now()
                 WHERE id = %s
                """, (row["id"],))
            tally["alive"] += 1
            log.info("  #%s is still running, but outside our search (%s)",
                     row["id"], why)
        elif verdict in (EXPIRED, DELETED, ABSENT):
            reason = {EXPIRED: "expired", DELETED: "deleted"}.get(
                verdict, spec.reason_when_absent)
            conn.execute(
                """
                UPDATE listings
                   SET status = 'gone', status_reason = %s, active = false,
                       -- The ad ended when we last saw it, not when we noticed.
                       removed_at = COALESCE(removed_at, missing_since, now()),
                       status_checked_at = now()
                 WHERE id = %s
                """, (reason, row["id"]))
            tally["gone"] += 1
            log.info("  #%s ended: %s (%s)", row["id"], reason, why)
        else:
            # Leave it missing so the next run tries again; record the attempt
            # so one unreadable page cannot be retried forever within a run.
            conn.execute(
                "UPDATE listings SET status_checked_at = now() WHERE id = %s",
                (row["id"],))
            tally["unknown"] += 1
            log.warning("  #%s inconclusive: %s", row["id"], why)
        conn.commit()

    return tally

"""Generic crawl engine.

Site-agnostic: everything it knows comes from a SourceConfig loaded out of
gitignored YAML. Politeness is structural, not optional — a randomised delay
sits between every request and page/detail budgets cap each run.
"""
from __future__ import annotations

import logging
import os
import random
import time
import urllib.robotparser

from playwright.sync_api import sync_playwright

from . import dedup, dedup_pass, extract, gone, images, json_api, storage
from .pagination import page_url
from .source_config import SourceConfig

log = logging.getLogger("crawler")


class Crawler:
    def __init__(self, cfg: SourceConfig, conn):
        self.cfg = cfg
        self.conn = conn
        self.requests = 0
        self._robots = self._load_robots() if cfg.respect_robots else None

    def _load_robots(self):
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{self.cfg.base_url}/robots.txt")
        try:
            rp.read()
            return rp
        except Exception as exc:
            log.warning("robots.txt unreadable (%s); proceeding at configured rate", exc)
            return None

    def _allowed(self, url: str) -> bool:
        return self._robots.can_fetch("*", url) if self._robots else True

    def _pause(self) -> None:
        time.sleep(random.uniform(self.cfg.delay_min, self.cfg.delay_max))

    def _fetch(self, page, url: str) -> str | None:
        if not self._allowed(url):
            log.info("robots.txt disallows %s — skipping", url)
            return None
        self._pause()
        self.requests += 1
        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        if self.cfg.ready_selector:
            try:
                page.wait_for_selector(self.cfg.ready_selector, timeout=15_000)
            except Exception:
                log.warning("ready_selector %r never appeared on %s",
                            self.cfg.ready_selector, url)
        # On a client-rendered site the document is complete long before the
        # content exists: reading here yields an empty shell and every selector
        # misses. Wait for the network to settle before snapshotting the DOM.
        if self.cfg.wait_for_idle:
            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass          # ad/analytics traffic can keep a page never idle
        return page.content()

    def run_api(self, page) -> dict:
        """List phase driven by a JSON endpoint instead of results HTML."""
        api = self.cfg.api
        self.touched = []
        seen_ids: list[str] = []
        new_ads = updated = pages = 0
        detail_budget = self.cfg.max_details if api.fetch_detail else 0

        # Some endpoints reject requests that arrive without a session cookie.
        if api.session_url:
            log.info("establishing session via %s", api.session_url.split("?")[0])
            self._fetch(page, api.session_url)

        run_id = storage.start_run(self.conn, self.cfg.key, self.cfg.searches[0].label)
        error = None
        try:
            page_no, total = 1, None
            while page_no <= self.cfg.max_pages:
                self._pause()
                self.requests += 1
                try:
                    rows, nxt, total = json_api.fetch_page(
                        page.context.request, api, page_no)
                except RuntimeError as exc:
                    # The session cookie expires part-way through a longer crawl:
                    # early pages succeed, then the endpoint starts returning 400.
                    # Re-establish it once and retry this page before giving up.
                    if not api.session_url or "400" not in str(exc) and "403" not in str(exc):
                        raise
                    log.warning("page %d rejected — refreshing session and retrying",
                                page_no)
                    self._fetch(page, api.session_url)
                    self._pause()
                    self.requests += 1
                    rows, nxt, total = json_api.fetch_page(
                        page.context.request, api, page_no)
                pages += 1
                log.info("api page %d: %d record(s)%s", page_no, len(rows),
                         f" of {total} total" if total else "")
                if not rows:
                    break

                for raw in rows:
                    rec = json_api.map_record(api, raw)
                    if not rec.get("source_id") or not rec.get("title"):
                        continue
                    seen_ids.append(rec["source_id"])

                    is_new = storage.listing_is_new(self.conn, self.cfg.key, rec["source_id"])
                    listing_id = storage.upsert_listing(
                        self.conn, self.cfg.key, rec["source_id"],
                        rec.get("url") or "", self.cfg.category)

                    # The list API carries price/area/location but not the
                    # description, room count or floor — those decide whether an
                    # ad is worth looking at, so fetch the page when asked.
                    if (api.fetch_detail and rec.get("url") and detail_budget > 0
                            and storage.needs_detail(
                                self.conn, listing_id, rec,
                                int(os.getenv("DETAIL_REFRESH_DAYS", "14")))):
                        html = self._fetch(page, rec["url"])
                        if html:
                            detail_budget -= 1
                            extra = extract.extract_detail(html, self.cfg, rec["url"])
                            # API values win: they are typed and already correct.
                            for k, v in extra.items():
                                if v not in (None, "", []) and rec.get(k) in (None, "", []):
                                    rec[k] = v

                    # Keep the untouched API payload: re-parsing later never
                    # needs another request.
                    rec["_api"] = raw
                    obs_id = storage.insert_observation(
                        self.conn, listing_id, rec, self.cfg.boilerplate)
                    if obs_id:
                        updated += 1
                        new_ads += int(is_new)
                        self.touched.append(listing_id)
                        storage.save_images(self.conn, listing_id, rec.get("images") or [])
                        clean = dedup.strip_boilerplate(
                            rec.get("description") or "", self.cfg.boilerplate)
                        storage.save_blocking_keys(self.conn, listing_id, rec, clean)
                    self.conn.commit()

                if nxt is None:
                    break
                page_no = nxt
        except Exception as exc:
            error = str(exc)
            log.exception("api crawl failed")
        finally:
            storage.finish_run(self.conn, run_id, pages=pages, requests=self.requests,
                               new_ads=new_ads, updated_ads=updated, error=error)
            self.conn.commit()

        missing = storage.mark_missing(self.conn, self.cfg.key, seen_ids)
        self.conn.commit()
        # Absence only raises the question; the ad's own page answers it.
        ended = gone.verify(self.conn, self.cfg, page)
        return {"mode": "json_api", "pages": pages, "requests": self.requests,
                "new_ads": new_ads, "observations": updated,
                "seen": len(seen_ids), "details_fetched": (
                    self.cfg.max_details - detail_budget) if api.fetch_detail else 0,
                "went_missing": missing, "status_checks": ended}

    def run(self, page) -> dict:
        if self.cfg.api:
            return self.run_api(page)

        seen_ids: list[str] = []
        self.touched: list[int] = []      # listings with a new observation this run
        new_ads = updated = pages = 0

        for search in self.cfg.searches:
            run_id = storage.start_run(self.conn, self.cfg.key, search.label)
            error = None
            try:
                # Keyed by source_id: one ad card usually carries several
                # anchors (image, title, "see more"), and promoted ads repeat on
                # every page. Counting those as separate candidates burns the
                # detail budget on ads we already hold.
                candidates: dict[str, dict] = {}
                for n in range(1, self.cfg.max_pages + 1):
                    html = self._fetch(page, page_url(self.cfg, search.url, n))
                    if not html:
                        break
                    pages += 1
                    found = extract.extract_list(html, self.cfg)
                    if not found:
                        log.warning(
                            "no items matched list.item=%r on page %d of %s — "
                            "the selector is probably stale",
                            self.cfg.list_item, n, search.label)
                        break
                    fresh = {c["source_id"]: c for c in found
                             if c["source_id"] not in candidates}
                    log.info("%s page %d: %d item(s), %d new (running total %d)",
                             search.label, n, len(found), len(fresh),
                             len(candidates) + len(fresh))
                    if not fresh:
                        # Asking for a page past the last one does not error —
                        # the site quietly serves page 1 again. Without this the
                        # crawl keeps paying for pages it has already read.
                        log.info("page %d repeats ads already seen — end of results",
                                 n)
                        break
                    candidates.update(fresh)

                seen_ids += list(candidates)

                # Spend the detail budget on ads that can still teach us
                # something. Never-seen ads come first: taking the list in page
                # order instead means a budget smaller than the result set can
                # never reach the last page, so new ads stay invisible forever.
                pending = [c for c in candidates.values()
                           if storage.needs_detail(
                               self.conn,
                               storage.listing_id_for(self.conn, self.cfg.key,
                                                      c["source_id"]),
                               c, int(os.getenv("DETAIL_REFRESH_DAYS", "14")))]
                skipped = len(candidates) - len(pending)
                if skipped:
                    log.info("%s: %d ad(s) unchanged since last run, skipping detail",
                             search.label, skipped)
                budget = self.cfg.max_details
                if len(pending) > budget:
                    log.warning("%s: %d ad(s) need detail but budget is %d — "
                                "raise max_details_per_run to finish in one pass",
                                search.label, len(pending), budget)
                for cand in pending[:budget]:
                    detail_html = self._fetch(page, cand["url"])
                    if not detail_html:
                        continue
                    rec = extract.extract_detail(detail_html, self.cfg, cand["url"])
                    if not rec.get("title"):
                        log.warning("detail.title matched nothing at %s", cand["url"])
                        continue

                    is_new = storage.listing_is_new(self.conn, self.cfg.key,
                                                    cand["source_id"])
                    listing_id = storage.upsert_listing(
                        self.conn, self.cfg.key, cand["source_id"], cand["url"],
                        self.cfg.category)
                    obs_id = storage.insert_observation(
                        self.conn, listing_id, rec, self.cfg.boilerplate)
                    if obs_id:
                        # Content changed (or this is the first sighting).
                        updated += 1
                        new_ads += int(is_new)
                        self.touched.append(listing_id)
                        storage.save_images(self.conn, listing_id, rec.get("images") or [])
                        clean = dedup.strip_boilerplate(
                            rec.get("description") or "", self.cfg.boilerplate)
                        storage.save_blocking_keys(self.conn, listing_id, rec, clean)
                    self.conn.commit()
            except Exception as exc:            # one search failing must not kill the run
                error = str(exc)
                log.exception("search %s failed", search.label)
            finally:
                storage.finish_run(self.conn, run_id, pages=pages, requests=self.requests,
                                   new_ads=new_ads, updated_ads=updated, error=error)
                self.conn.commit()

        missing = storage.mark_missing(self.conn, self.cfg.key, seen_ids)
        self.conn.commit()
        # Absence only raises the question; the ad's own page answers it.
        ended = gone.verify(self.conn, self.cfg, page)
        return {"pages": pages, "requests": self.requests, "new_ads": new_ads,
                "observations": updated, "went_missing": missing,
                "status_checks": ended}


def verify_gone_all(configs: list[SourceConfig]) -> dict:
    """Settle the status of every ad that went missing, without crawling.

    Worth running on its own: the check is cheap next to a crawl, and after a
    selector fix it is how a pile of wrongly-flagged listings gets put right.
    """
    out: dict[str, dict] = {}
    with sync_playwright() as pw, storage.connect() as conn:
        for cfg in configs:
            if cfg.gone is None:
                continue
            headless = cfg.headless
            ctx = _open_context(pw, headless, cfg.key)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                # A per-ad API probe usually needs the same session cookie the
                # list phase relies on.
                if cfg.gone.api_probe_url and cfg.api and cfg.api.session_url:
                    page.goto(cfg.api.session_url, wait_until="domcontentloaded",
                              timeout=45_000)
                out[cfg.key] = gone.verify(conn, cfg, page)
            except Exception:
                # One source's failure must not poison the connection for the
                # next: an aborted transaction rejects every later statement.
                conn.rollback()
                log.exception("status check failed for %s", cfg.key)
            finally:
                ctx.close()
    return out


def _profile_for(key: str) -> str:
    """One browser profile per source.

    Chromium locks a profile directory while it is open, so a shared profile
    means a scheduled crawl blocks every one-off command. Separate directories
    also keep each site's cookies to itself.
    """
    root = os.getenv("BROWSER_PROFILE_DIR", "/data/browser-profile")
    path = os.path.join(root, key)
    os.makedirs(path, exist_ok=True)
    # A crash leaves a lock behind that would block every later run.
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            os.remove(os.path.join(path, lock))
        except OSError:
            pass
    return path


def _open_context(pw, headless: bool, key: str):
    # A PERSISTENT profile keeps cookies and any clearance token between runs,
    # so we look like one returning human rather than a fresh client.
    env_headless = os.getenv("BROWSER_HEADLESS", "true").lower() != "false"
    return pw.chromium.launch_persistent_context(
        _profile_for(key), headless=headless and env_headless,
        locale=os.getenv("BROWSER_LOCALE", "hr-HR"),
        timezone_id=os.getenv("BROWSER_TIMEZONE", "Europe/Zagreb"),
        viewport={"width": 1440, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )


def crawl_all(configs: list[SourceConfig]) -> None:
    env_headless = os.getenv("BROWSER_HEADLESS", "true").lower() != "false"

    with sync_playwright() as pw, storage.connect() as conn:
        touched: list[int] = []
        ctx = None
        try:
            for cfg in configs:
                # Each source may need a different browser mode, and only one
                # persistent context can hold the profile at a time.
                want_headless = cfg.headless and env_headless
                if ctx is not None:
                    ctx.close()
                ctx = _open_context(pw, cfg.headless, cfg.key)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()

                log.info("crawling %s (%d searches, %s browser)", cfg.name,
                         len(cfg.searches), "headless" if want_headless else "headed")
                crawler = Crawler(cfg, conn)
                stats = crawler.run(page)
                touched += crawler.touched
                log.info("%s done: %s", cfg.name, stats)

            # Image hashing runs after collection, not inline: a slow image host
            # must never stall page crawling, and this can be re-run to fill gaps.
            if touched:
                img_stats = images.backfill(conn, ctx.request, touched)
                log.info("images: %s", img_stats)
                # Blocking keys were written before any hash existed, so the
                # phash bands have to be added now or the strongest dedup signal
                # never reaches candidate generation.
                images.refresh_blocking_keys(conn, touched)
        finally:
            if ctx is not None:
                ctx.close()

        # Dedup last, so it sees this run's observations AND their image hashes.
        if touched:
            dedup_pass.run(conn, touched)

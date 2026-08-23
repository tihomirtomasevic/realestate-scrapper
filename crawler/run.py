"""Crawler entrypoint.

    python -m crawler.run --validate    # parse configs, hit one page, report selectors
    python -m crawler.run --once        # one pass, then exit
    python -m crawler.run               # loop on CRAWL_INTERVAL_MINUTES
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from .source_config import ConfigError, load_all

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("crawler.run")


def validate() -> int:
    """Parse every config and report what each selector matches on one live page.

    Lets you iterate on selectors without running a full crawl — the selectors
    are the part that breaks when a site changes its markup.
    """
    from playwright.sync_api import sync_playwright

    from . import extract
    from .engine import Crawler
    from .source_config import FieldSpec

    configs = load_all()
    print(f"parsed {len(configs)} source config(s)\n")
    failures = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(locale=os.getenv("BROWSER_LOCALE", "hr-HR"))
        for cfg in configs:
            print(f"── {cfg.name} [{cfg.key}] → {cfg.host}")
            print(f"   {len(cfg.searches)} search(es), "
                  f"delay {cfg.delay_min}-{cfg.delay_max}s, "
                  f"max {cfg.max_pages} pages / {cfg.max_details} details")
            crawler = Crawler.__new__(Crawler)   # no DB connection needed
            crawler.cfg, crawler.requests = cfg, 0
            crawler._robots = crawler._load_robots() if cfg.respect_robots else None

            search = cfg.searches[0]
            try:
                html = crawler._fetch(page, search.url)
            except Exception as exc:
                print(f"   FETCH FAILED: {exc}\n")
                failures += 1
                continue
            if html is None:
                print("   robots.txt disallows this search URL\n")
                failures += 1
                continue

            items = extract.extract_list(html, cfg)
            status = "ok" if items else "NO MATCHES — selector is stale"
            print(f"   list.item {cfg.list_item!r} → {len(items)} item(s)  [{status}]")
            if not items:
                failures += 1
                print()
                continue

            detail_html = crawler._fetch(page, items[0]["url"])
            rec = extract.extract_detail(detail_html, cfg, items[0]["url"])
            for field in ("title", "price_eur", "area_m2", "rooms", "floor",
                          "location_raw", "seller_name", "seller_type"):
                if field.replace("_eur", "").replace("_raw", "") in (
                        k.replace("_m2", "") for k in cfg.detail):
                    value = rec.get(field)
                    mark = "ok " if value not in (None, "") else "MISS"
                    print(f"   [{mark}] {field:<14} {str(value)[:60]}")
            print(f"   [{'ok ' if rec.get('images') else 'MISS'}] "
                  f"{'images':<14} {len(rec.get('images') or [])} found")
            if not rec.get("title"):
                failures += 1
            print()
        browser.close()

    print("validation passed" if not failures else f"{failures} source(s) need attention")
    return 1 if failures else 0


def login(source_key: str) -> int:
    """Open a visible browser so a human can clear a bot challenge once.

    The clearance cookie lands in the persistent profile, which later headless
    runs reuse — so this is a one-off, not a per-crawl step. Needs a display
    (on WSL, WSLg provides one; pass DISPLAY and /tmp/.X11-unix into the
    container).
    """
    from playwright.sync_api import sync_playwright

    from .source_config import ConfigError, load_all

    try:
        cfg = next(c for c in load_all("*") if c.key == source_key)
    except (ConfigError, StopIteration):
        log.error("unknown source %r", source_key)
        return 2

    if not os.getenv("DISPLAY"):
        log.error("no DISPLAY set — run with:\n"
                  "  docker compose run --rm -e DISPLAY=$DISPLAY "
                  "-v /tmp/.X11-unix:/tmp/.X11-unix crawler "
                  "python -m crawler.run --login %s", source_key)
        return 2

    # Same per-source profile the crawler uses, so cleared cookies are reused.
    profile = os.path.join(
        os.getenv("BROWSER_PROFILE_DIR", "/data/browser-profile"), cfg.key)
    os.makedirs(profile, exist_ok=True)
    url = cfg.searches[0].url
    print(f"\nOpening {cfg.name} in a visible browser.")
    print("Solve any challenge that appears, then leave the results page open.")
    print("Press Enter here when the listings are visible.\n")

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            profile, headless=False,
            locale=os.getenv("BROWSER_LOCALE", "hr-HR"),
            timezone_id=os.getenv("BROWSER_TIMEZONE", "Europe/Zagreb"),
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)
        try:
            input()
        except EOFError:
            page.wait_for_timeout(120_000)

        from . import blockcheck

        reason = blockcheck.detect(page)
        evidence = blockcheck.content_evidence(page, cfg.host)
        cookies = [c["name"] for c in ctx.cookies()]

        print(f"\npage: {page.title()[:70]!r}")
        print(f"content: {evidence['text_chars']} chars of text, "
              f"{evidence['internal_links']} internal links, "
              f"{evidence['real_images']} images")

        if reason:
            print(f"\nStill a challenge ({reason}) — clearance not saved.")
            ctx.close()
            return 1

        print(f"cookies saved to the profile: {len(cookies)} — {cookies[:10]}")
        print("\nCleared. Headless runs reusing this profile should now get real pages.")
        ctx.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="crawler.run")
    ap.add_argument("--validate", action="store_true",
                    help="check configs and selectors against one live page, then exit")
    ap.add_argument("--once", action="store_true", help="single pass, then exit")
    ap.add_argument("--dedup-only", action="store_true",
                    help="re-score and re-cluster everything already stored, no crawling")
    ap.add_argument("--phash-only", action="store_true",
                    help="hash images that have no perceptual hash yet, then exit")
    ap.add_argument("--verify-gone", action="store_true",
                    help="re-check ads that dropped out of the search results and "
                         "settle whether they actually ended, then exit")
    ap.add_argument("--login", metavar="SOURCE",
                    help="open a visible browser on this source's first search so a "
                         "bot challenge can be solved by hand; the persistent profile "
                         "keeps the clearance for later headless runs")
    args = ap.parse_args()

    if args.login:
        return login(args.login)

    # These operate on stored data and need no source configs.
    if args.dedup_only:
        from . import dedup_pass, storage
        with storage.connect() as conn:
            print(dedup_pass.run(conn))
        return 0

    if args.phash_only:
        from playwright.sync_api import sync_playwright

        from . import images, storage
        with sync_playwright() as pw, storage.connect() as conn:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(locale=os.getenv("BROWSER_LOCALE", "hr-HR"))
            try:
                print(images.backfill(conn, ctx.request))
                touched = [r["listing_id"] for r in conn.execute(
                    "SELECT DISTINCT listing_id FROM images "
                    "WHERE phash IS NOT NULL AND phash <> ''").fetchall()]
                images.refresh_blocking_keys(conn, touched)
            finally:
                browser.close()
        return 0

    try:
        configs = load_all()
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    if args.validate:
        return validate()

    if args.verify_gone:
        from .engine import verify_gone_all
        print(verify_gone_all(configs))
        return 0

    from .engine import crawl_all
    from . import storage

    interval = int(os.getenv("CRAWL_INTERVAL_MINUTES", "180")) * 60
    poll = int(os.getenv("TRIGGER_POLL_SECONDS", "10"))

    if args.once:
        crawl_all(configs)
        return 0

    log.info("scheduler: every %d min; on-demand requests checked every %ds",
             interval // 60, poll)
    next_run = 0.0        # run once at startup, then on the interval

    while True:
        now = time.monotonic()

        # An on-demand request jumps the queue.
        request = None
        try:
            with storage.connect() as conn:
                request = storage.claim_crawl_request(conn)
        except Exception:
            log.exception("could not check for crawl requests")

        due = now >= next_run
        if request or due:
            picked = configs
            if request and request["source"]:
                picked = [c for c in configs if c.key == request["source"]] or configs
            trigger = f"request #{request['id']}" if request else "schedule"
            log.info("crawl start (%s): %s", trigger,
                     ", ".join(c.key for c in picked))

            result, error = None, None
            started = time.monotonic()
            try:
                crawl_all(picked)
                result = {"sources": [c.key for c in picked],
                          "seconds": round(time.monotonic() - started)}
            except Exception as exc:
                error = str(exc)
                log.exception("crawl pass failed; continuing")

            if request:
                try:
                    with storage.connect() as conn:
                        storage.finish_crawl_request(conn, request["id"], result, error)
                except Exception:
                    log.exception("could not record crawl request outcome")

            # A manual run also satisfies the schedule — no point repeating
            # the same work minutes later.
            next_run = time.monotonic() + interval

        time.sleep(poll)


if __name__ == "__main__":
    sys.exit(main())

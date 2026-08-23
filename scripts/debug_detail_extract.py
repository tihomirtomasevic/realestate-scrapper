"""Run the real extraction path against one detail page and show what matched.

    docker compose run --rm crawler python /app/scripts/debug_detail_extract.py
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, "/app")
from crawler import extract, storage  # noqa: E402
from crawler.source_config import load_all  # noqa: E402


def main() -> int:
    # Optional source key; defaults to the first one configured locally so no
    # target is named in a tracked file.
    configs = load_all("*")
    key = sys.argv[1] if len(sys.argv) > 1 else configs[0].key
    cfg = next(c for c in configs if c.key == key)
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT url FROM listings WHERE url <> '' ORDER BY id LIMIT 1").fetchone()
    url = row["url"]
    print(f"url: {url[-70:]}\nready_selector: {cfg.ready_selector!r}\n")

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        ctx = b.new_context(locale="hr-HR", timezone_id="Europe/Zagreb",
                            viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        # Exactly what Crawler._fetch does.
        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        if cfg.ready_selector:
            try:
                page.wait_for_selector(cfg.ready_selector, timeout=15_000)
                print(f"ready_selector {cfg.ready_selector!r} appeared")
            except Exception:
                print(f"ready_selector {cfg.ready_selector!r} NEVER APPEARED (15s wasted)")
        html_fast = page.content()

        # Then the same page after the network settles.
        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        page.mouse.wheel(0, 1500)
        page.wait_for_timeout(1500)
        html_full = page.content()

        print(f"\nhtml at domcontentloaded: {len(html_fast)}b")
        print(f"html after networkidle  : {len(html_full)}b  "
              f"(+{len(html_full) - len(html_fast)})")

        for label, html in (("FAST (what the crawler used)", html_fast),
                            ("FULL (after networkidle)", html_full)):
            rec = extract.extract_detail(html, cfg, url)
            print(f"\n── {label} ──")
            for k in ("title", "description", "rooms", "floor", "area_m2",
                      "seller_name", "seller_phone"):
                v = rec.get(k)
                shown = (v[:60] + "…") if isinstance(v, str) and len(v) > 60 else v
                mark = "ok  " if v not in (None, "", []) else "MISS"
                print(f"  [{mark}] {k:<14} {shown!r}")
            print(f"  images: {len(rec.get('images') or [])}")

        # Are the selectors present in the raw HTML at all?
        print("\n── selector presence in FULL html ──")
        from selectolax.parser import HTMLParser
        tree = HTMLParser(html_full)
        for name, spec in cfg.detail.items():
            if spec.selector:
                n = len(tree.css(spec.selector))
                print(f"  {name:<14} {n:>3} match(es)  {spec.selector[:50]}")
            elif spec.label:
                lines = extract.text_lines(tree)
                val = extract.by_label(lines, spec.label)
                print(f"  {name:<14} label {spec.label!r} -> {val!r}")
        b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

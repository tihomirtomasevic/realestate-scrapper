"""Discover whether a target site renders its results from a JSON endpoint.

Many classified sites are client-side apps backed by a JSON API. Calling that
API directly is far sturdier than parsing HTML — JSON field names change much
less often than CSS classes — so it is worth checking before writing selectors.

Reads the target from a (gitignored) source config; nothing site-specific is
hardcoded here.

    docker compose run --rm crawler python /app/scripts/discover_api.py [source-key]

Loads a handful of pages. Reports: candidate endpoints, whether a session cookie
is required, the record shape, the detail-page URL pattern, and how image URLs
are built.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.robotparser
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

sys.path.insert(0, "/app")
from crawler.source_config import load_all  # noqa: E402

RESULT_KEYS = ("items", "results", "ads", "data", "hits", "listings",
               "content", "documents", "records", "oglasi")


def result_array(payload):
    """Find the array of records in an arbitrary JSON envelope."""
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload, "(top level)"
    if isinstance(payload, dict):
        for k in RESULT_KEYS:
            v = payload.get(k)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v, k
            if isinstance(v, dict):
                for k2 in RESULT_KEYS:
                    v2 = v.get(k2)
                    if isinstance(v2, list) and v2 and isinstance(v2[0], dict):
                        return v2, f"{k}.{k2}"
    return None, None


# A config/taxonomy endpoint can easily return more rows than the results
# endpoint, so rank by how much a record LOOKS like a classified ad, not by size.
AD_SIGNALS = ("price", "cijena", "title", "naslov", "image", "slik", "photo",
              "area", "povrsina", "kvadratura", "location", "lokacija", "posted",
              "created", "url", "link", "slug")
CONFIG_SIGNALS = ("displayname", "icon", "modulehr", "namehr", "is18", "translation")


def ad_likeness(rows: list[dict]) -> int:
    keys = {k.lower() for k in rows[0]}
    score = sum(2 for s in AD_SIGNALS if any(s in k for k in keys))
    score -= sum(3 for s in CONFIG_SIGNALS if any(s in k for k in keys))
    return score


def main() -> int:
    key = sys.argv[1] if len(sys.argv) > 1 else None
    configs = load_all("*")
    cfg = next((c for c in configs if c.key == key), configs[0])
    search = cfg.searches[0]
    origin = f"{urlparse(cfg.base_url).scheme}://{cfg.host}"
    print(f"source : {cfg.name} [{cfg.key}]\nhost   : {cfg.host}\nsearch : {search.label}\n")

    # Sites that fingerprint the browser reject headless outright, so allow a
    # real headed browser on a virtual display (see BROWSER_HEADLESS).
    headless = os.getenv("BROWSER_HEADLESS", "true").lower() != "false"
    print(f"browser: {'headless' if headless else 'headed (virtual display)'}\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = browser.new_context(locale="hr-HR", timezone_id="Europe/Zagreb",
                                  viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        api_calls: list[str] = []
        page.on("request", lambda r: api_calls.append(r.url)
                if r.resource_type in ("xhr", "fetch") and cfg.host in r.url else None)

        print("── 1. loading the search page, watching its XHR ──")
        resp = page.goto(search.url, wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_load_state("networkidle", timeout=25_000)
        except Exception:
            pass
        page.wait_for_timeout(2000)

        # A blocked page has no XHR worth inspecting. Without this check the
        # tool reports "no API" when the truth is "never saw the site" — a
        # false negative that is worse than no answer.
        from crawler import blockcheck
        reason = blockcheck.detect(page, resp)
        ev = blockcheck.content_evidence(page, cfg.host)
        if reason:
            print(f"  BLOCKED ({reason}) — status={resp.status if resp else '?'}")
            print(f"  content seen: {ev['text_chars']} chars, "
                  f"{ev['internal_links']} internal links")
            print("  No conclusion can be drawn about APIs from a blocked page.")
            browser.close()
            return 2
        print(f"  page ok: status={resp.status if resp else '?'} "
              f"title={page.title()[:50]!r}")
        print(f"  content: {ev['text_chars']} chars, {ev['internal_links']} "
              f"internal links, {ev['real_images']} images")

        endpoints: list[tuple[str, list, str]] = []
        for url in dict.fromkeys(api_calls):
            try:
                r = ctx.request.get(url, timeout=20_000)
                if r.status >= 400:
                    continue
                rows, where = result_array(r.json())
            except Exception:
                continue
            if rows:
                endpoints.append((url, rows, where))

        if not endpoints:
            print("  no JSON endpoint returned a result set — HTML selectors it is.")
            browser.close()
            return 1

        ranked = sorted(endpoints, key=lambda e: (ad_likeness(e[1]), len(e[1])), reverse=True)
        if len(ranked) > 1:
            print(f"  {len(ranked)} JSON endpoint(s) returned arrays; ranked by ad-likeness:")
            for u, rws, _ in ranked[:5]:
                print(f"    score {ad_likeness(rws):>3}  {len(rws):>4} rec  {urlparse(u).path}")
        url, rows, where = ranked[0]
        path = urlparse(url).path
        print(f"\n  RESULTS ENDPOINT: {path}")
        print(f"    records at {where}[]: {len(rows)}")
        print(f"    query: {urlparse(url).query[:220]}")

        print("\n── 2. robots.txt ──")
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{origin}/robots.txt")
        try:
            rp.read()
            print(f"  allows {path}: {rp.can_fetch('*', url)}")
        except Exception as exc:
            print(f"  unreadable: {exc}")

        print("\n── 3. does the endpoint need a session? ──")
        fresh = browser.new_context(locale="hr-HR")
        cold = fresh.request.get(url, timeout=20_000)
        print(f"  cold (new context, no page visit): {cold.status}")
        fresh.close()
        warm = ctx.request.get(url, timeout=20_000)
        print(f"  warm (after loading a page):       {warm.status}")
        if cold.status >= 400 <= warm.status:
            pass
        if cold.status >= 400 and warm.status < 400:
            print(f"  => SESSION REQUIRED. cookies: {[c['name'] for c in ctx.cookies()][:8]}")
            print("     the crawler must load a page once before calling the API")
        else:
            print("  => callable without a session")

        print("\n── 4. record shape ──")
        rec = rows[0]
        print(json.dumps(rec, indent=2, ensure_ascii=False)[:1800])
        cov: dict[str, int] = {}
        for row in rows:
            for k, v in row.items():
                if v not in (None, "", [], {}):
                    cov[k] = cov.get(k, 0) + 1
        print(f"\n  field coverage across {len(rows)} records:")
        for k in sorted(cov):
            print(f"    {k:<30} {cov[k]}/{len(rows)}")

        print("\n── 5. detail URL pattern (read off a rendered result link) ──")
        hrefs = page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.getAttribute('href'))")
        # Only consider links that go somewhere per-ad. Without this the price
        # ("230000") matches inside the search URL and reports a false pattern.
        per_ad = [h for h in hrefs
                  if h and "?" not in h and any(seg in h for seg in ("/oglas/", "/ad/", "/item/"))]

        detail_href = None
        for field, value in rec.items():
            if not isinstance(value, (int, str)):
                continue
            token = str(value)
            if len(token) < 5:
                continue
            hits = [h for h in per_ad if token in h]
            if hits:
                detail_href = hits[0]
                print(f"  record field {field!r} = {token} appears in a detail link:")
                print(f"    {detail_href}")
                # Report the pattern with every recognised field substituted.
                pattern = detail_href
                for f2, v2 in rec.items():
                    if isinstance(v2, (int, str)) and len(str(v2)) >= 4 and str(v2) in pattern:
                        pattern = pattern.replace(str(v2), "{" + f2 + "}")
                print(f"  => detail_url_template: {origin}{pattern}")
                break
        if not detail_href:
            print(f"  no per-ad link matched a record field "
                  f"({len(per_ad)} per-ad links seen) — links may be JS-driven")

        print("\n── 6. image URLs (observed, not guessed) ──")
        if detail_href:
            detail_url = detail_href if detail_href.startswith("http") else origin + detail_href
            d = ctx.new_page()
            seen_imgs: list[str] = []
            d.on("request", lambda r: seen_imgs.append(r.url)
                 if r.resource_type == "image" else None)
            d.goto(detail_url, wait_until="domcontentloaded", timeout=60_000)
            try:
                d.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass
            d.mouse.wheel(0, 1500)
            d.wait_for_timeout(2500)
            print(f"  detail page: {d.title()[:80]!r}")

            # Match observed image requests against any id-like string in the record.
            frags = []
            for v in rec.values():
                if isinstance(v, list):
                    frags += [str(x) for x in v if isinstance(x, str)]
            matched = [u for u in seen_imgs if any(f.split("/")[-1] in u for f in frags)]
            for u in (matched or [u for u in seen_imgs if "assets" not in u])[:5]:
                print(f"    {'MATCH ' if matched else ''}{u[:160]}")
            if matched and frags:
                frag = next(f for f in frags if f.split("/")[-1] in matched[0])
                print(f"\n  => template: {matched[0].replace(frag, '{value}')[:160]}")

            text = d.eval_on_selector("body", "e => e.innerText") or ""
            paras = [p.strip() for p in text.split("\n") if len(p.strip()) > 90]
            print(f"\n  detail page carries {len(paras)} long paragraph(s) "
                  f"(description lives here, not in the list API)")
            d.close()

        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

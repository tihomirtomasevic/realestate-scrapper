"""Work out what it takes to reach a site that blocks the default crawler,
and whether an API is reachable behind the block.

Headless Chromium advertises itself in several ways (a HeadlessChrome UA,
navigator.webdriver, the automation flag). This tries progressively more
realistic configurations and reports which, if any, is served real content —
then looks for a JSON endpoint from whichever one worked.

    docker compose run --rm crawler python /app/scripts/probe_blocked.py [source-key]
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, "/app")
from crawler.source_config import load_all  # noqa: E402

REAL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

BLOCK_MARKERS = ("captcha", "just a moment", "cf-challenge", "shieldsquare",
                 "access denied", "are you a human", "unusual traffic",
                 "attention required", "blocked")

STEALTH = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['hr-HR','hr','en-US']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = window.chrome || {runtime: {}};
"""


def blocked(page, resp) -> str | None:
    title = (page.title() or "").lower()
    body = (page.content() or "").lower()[:200_000]
    if resp and resp.status in (403, 429):
        return f"status {resp.status}"
    return next((m for m in BLOCK_MARKERS if m in body or m in title), None)


def attempt(pw, label: str, url: str, *, stealth: bool, ua: str | None,
            headless: bool = True) -> tuple[bool, object, object]:
    browser = pw.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"] if stealth else [],
    )
    ctx = browser.new_context(
        locale="hr-HR", timezone_id="Europe/Zagreb",
        viewport={"width": 1440, "height": 900},
        user_agent=ua,
        extra_http_headers={"Accept-Language": "hr-HR,hr;q=0.9,en;q=0.8"} if stealth else {},
    )
    if stealth:
        ctx.add_init_script(STEALTH)
    page = ctx.new_page()
    xhr: list[str] = []
    page.on("request", lambda r: xhr.append(r.url)
            if r.resource_type in ("xhr", "fetch") else None)

    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        page.wait_for_timeout(2000)
        why = blocked(page, resp)
        status = resp.status if resp else "?"
        if why:
            print(f"  [{label:<28}] BLOCKED  status={status}  ({why})")
            browser.close()
            return False, None, None
        print(f"  [{label:<28}] OK       status={status}  "
              f"title={page.title()[:44]!r}")
        return True, (browser, ctx, page), xhr
    except Exception as exc:
        print(f"  [{label:<28}] ERROR    {str(exc)[:70]}")
        try:
            browser.close()
        except Exception:
            pass
        return False, None, None


def main() -> int:
    # Default to the first locally configured source rather than naming one:
    # the targets live in gitignored config, and so should their names.
    configs = load_all("*")
    key = sys.argv[1] if len(sys.argv) > 1 else configs[0].key
    cfg = next(c for c in configs if c.key == key)
    url = cfg.searches[0].url
    print(f"target: {cfg.name} [{cfg.key}]\nsearch: {url[:110]}\n")

    with sync_playwright() as pw:
        print("── which browser configuration gets served real content? ──")
        tries = [
            ("default headless", dict(stealth=False, ua=None)),
            ("realistic UA", dict(stealth=False, ua=REAL_UA)),
            ("UA + stealth flags", dict(stealth=True, ua=REAL_UA)),
        ]
        live = None
        for label, kw in tries:
            ok, handles, xhr = attempt(pw, label, url, **kw)
            if ok:
                live = (handles, xhr)
                break

        if not live:
            print("\nEvery headless configuration was blocked.")
            print("Options:")
            print("  1. run headed with a persistent profile and solve the challenge once")
            print("  2. use the site's own saved-search email alerts for discovery")
            print("  3. check the mobile app's API with mitmproxy")
            # Even blocked, an API path may answer directly.
            print("\n── probing plausible API paths directly ──")
            ctx = pw.chromium.launch(headless=True).new_context(user_agent=REAL_UA)
            for path in ("/api/search", "/api/v1/search", "/api/classifieds",
                         "/api/ads", "/graphql", "/api/v2/search",
                         "/rest/search", "/_next/data"):
                try:
                    r = ctx.request.get(f"https://{cfg.host}{path}", timeout=12_000)
                    ct = (r.headers or {}).get("content-type", "").split(";")[0]
                    print(f"  {r.status}  {path:<22} {ct}")
                except Exception as exc:
                    print(f"  ERR  {path:<22} {str(exc)[:44]}")
            return 2

        (browser, ctx, page), xhr = live
        print(f"\n── XHR seen on the working config ({len(set(xhr))} unique) ──")
        own = [u for u in dict.fromkeys(xhr) if cfg.host in u]
        for u in own[:20]:
            print(f"  {u[:130]}")
        if not own:
            print("  none from this host — the page is server-rendered HTML")

        print("\n── do any return ad-shaped JSON? ──")
        for u in own:
            try:
                r = ctx.request.get(u, timeout=15_000)
                if r.status >= 400:
                    continue
                body = r.json()
            except Exception:
                continue
            node = body
            if isinstance(node, dict):
                for k in ("items", "results", "ads", "data", "hits", "oglasi"):
                    if isinstance(node.get(k), list) and node[k]:
                        print(f"  {u[:90]}\n    -> {k}[] with {len(node[k])} records, "
                              f"keys={sorted(node[k][0])[:10]}")
                        break
            elif isinstance(node, list) and node and isinstance(node[0], dict):
                print(f"  {u[:90]}\n    -> array of {len(node)}, keys={sorted(node[0])[:10]}")

        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

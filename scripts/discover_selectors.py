"""Propose list/detail selectors by inspecting a real search and detail page.

Guessing selectors from naming conventions wastes runs. This looks at the actual
DOM: it finds the repeated container that holds result cards, then reports the
labelled fields on a detail page so they can be mapped to config.

    docker compose run --rm crawler python /app/scripts/discover_selectors.py <source-key>
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter

from playwright.sync_api import sync_playwright

sys.path.insert(0, "/app")
from crawler.source_config import load_all  # noqa: E402

# Finds a repeated element that (a) occurs many times and (b) contains a link.
FIND_CARDS = """
() => {
  const counts = {};
  document.querySelectorAll('[class]').forEach(el => {
    if (!el.querySelector('a[href]')) return;
    el.classList.forEach(c => {
      if (!c || /^(is-|js-|has-)/.test(c)) return;
      const sel = el.tagName.toLowerCase() + '.' + CSS.escape(c);
      counts[sel] = (counts[sel] || 0) + 1;
    });
  });
  return Object.entries(counts)
    .filter(([, n]) => n >= 5 && n <= 200)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 25);
}
"""

# Pulls text of the first match so a human can tell what a selector actually holds.
SAMPLE = """
(sel) => {
  const el = document.querySelector(sel);
  return el ? (el.innerText || '').trim().slice(0, 220) : null;
}
"""


def main() -> int:
    key = sys.argv[1] if len(sys.argv) > 1 else None
    configs = load_all("*")
    cfg = next((c for c in configs if c.key == key), configs[0])
    search = cfg.searches[0]
    print(f"source: {cfg.name} [{cfg.key}]\nsearch: {search.url[:120]}\n")

    sys.path.insert(0, "/app/scripts")
    from _browser import goto_unblocked, open_page

    with sync_playwright() as pw:
        close, ctx, page = open_page(pw)
        resp, reason = goto_unblocked(page, search.url, host=cfg.host)
        if reason:
            print(f"  BLOCKED after retries ({reason}) — nothing to inspect")
            close()
            return 1
        html = page.content()
        print(f"status={resp.status if resp else '?'}  title={page.title()[:80]!r}  "
              f"html={len(html)}b")

        print("\n── repeated containers holding a link (result-card candidates) ──")
        for sel, n in page.evaluate(FIND_CARDS):
            txt = (page.evaluate(SAMPLE, sel) or "").replace("\n", " | ")[:110]
            print(f"  {n:>4}x  {sel:<46} {txt}")

        print("\n── links that look per-ad ──")
        hrefs = page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.getAttribute('href'))")
        pat = Counter()
        for h in hrefs:
            if not h or h.startswith(("#", "javascript:")):
                continue
            shape = re.sub(r"\d+", "N", re.sub(r"[a-z0-9-]{8,}", "SLUG", h.split("?")[0]))
            pat[shape] += 1
        for shape, n in pat.most_common(10):
            print(f"  {n:>4}x  {shape[:96]}")

        # Pick a plausible detail link. A per-ad URL carries the ad's numeric
        # id, so a long run of digits is the one signal that holds across sites
        # — anything more specific would be this repo naming a target.
        # Override with a regex argument when a site does something odder.
        ad_link = re.compile(sys.argv[2] if len(sys.argv) > 2 else r"\d{6,}")
        detail = None
        for h in hrefs:
            if h and ad_link.search(h) and "?" not in h:
                detail = h if h.startswith("http") else f"https://{cfg.host}{h}"
                break
        if not detail:
            print("\n  could not identify a detail link automatically")
            close()
            return 1

        print(f"\n── detail page ──\n  {detail[:120]}")
        d = ctx.new_page()
        r2 = d.goto(detail, wait_until="domcontentloaded", timeout=60_000)
        try:
            d.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        d.mouse.wheel(0, 1200)
        d.wait_for_timeout(2000)
        print(f"  status={r2.status if r2 else '?'}  title={d.title()[:80]!r}")

        print("\n  h1 / price / labelled rows:")
        for sel in ("h1", "[class*='price']", "[class*='Price']", "dl", "table"):
            n = len(d.query_selector_all(sel))
            if n:
                txt = (d.evaluate(SAMPLE, sel) or "").replace("\n", " | ")[:150]
                print(f"    {sel:<24} {n:>3}x  {txt}")

        print("\n  label → value pairs found in the page text:")
        text = d.eval_on_selector("body", "e => e.innerText") or ""
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        WANTED = ("soba", "kvadrat", "površina", "povrsina", "kat", "godina",
                  "stanje", "grijanje", "cijena", "lokacija", "tip", "oglas")
        for i, ln in enumerate(lines):
            if len(ln) < 34 and any(w in ln.lower() for w in WANTED):
                nxt = lines[i + 1] if i + 1 < len(lines) else ""
                if nxt and len(nxt) < 60:
                    print(f"    {ln:<34} → {nxt}")

        print("\n  images (non-asset):")
        srcs = d.eval_on_selector_all(
            "img", "els => els.map(e => e.currentSrc || e.src).filter(Boolean)")
        photos = [s for s in srcs if not re.search(r"\.svg|icon|logo|sprite", s, re.I)]
        for s in photos[:6]:
            print(f"    {s[:130]}")
        if photos:
            print(f"    ({len(photos)} total)")

        close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

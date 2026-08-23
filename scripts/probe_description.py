"""Locate the ad description across several detail pages.

Some ads genuinely have none, so one page is not enough evidence. This ignores
known boilerplate blocks (safety tips, similar-ad cards) and reports the class
of the longest remaining text on each page.

    docker compose run --rm crawler python /app/scripts/probe_description.py
"""
from __future__ import annotations

import re
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, "/app")
from crawler import storage  # noqa: E402

# Blocks that are page furniture, not the ad body.
NOISE = ("TipsSection", "similarAd", "priceComparison", "Footer", "Header",
         "cookie", "didomi", "Nav", "banner", "sellerSection", "sellerInfo")

LONGEST = """
(noise) => {
  let best = null;
  const SKIP = new Set(['STYLE','SCRIPT','NOSCRIPT','SVG','PATH','HEAD','TITLE']);
  document.querySelectorAll('*').forEach(el => {
    if (SKIP.has(el.tagName)) return;
    const cls = el.className && el.className.baseVal !== undefined
      ? el.className.baseVal : (el.className || '');
    if (typeof cls !== 'string') return;
    if (noise.some(n => cls.toLowerCase().includes(n.toLowerCase()))) return;
    const own = Array.from(el.childNodes)
      .filter(n => n.nodeType === 3).map(n => n.textContent).join(' ').trim();
    if (own.length < 60) return;
    if (!best || own.length > best.len) {
      best = {cls: cls, tag: el.tagName.toLowerCase(), len: own.length,
              text: own.slice(0, 260)};
    }
  });
  return best;
}
"""


def main() -> int:
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT l.id, l.url, o.title FROM listings l "
            "JOIN v_current o ON o.listing_id=l.id "
            "WHERE l.url <> '' ORDER BY l.id LIMIT 5").fetchall()

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        ctx = b.new_context(locale="hr-HR", timezone_id="Europe/Zagreb",
                            viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        found = []

        for row in rows:
            page.goto(row["url"], wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass
            page.mouse.wheel(0, 1800)
            page.wait_for_timeout(1800)

            # Some sites hide the body behind a "show more" control.
            for label in ("Prikaži više", "Prikaži cijeli", "Više", "Opširnije"):
                try:
                    btn = page.get_by_text(label, exact=False).first
                    if btn and btn.is_visible():
                        btn.click(timeout=2000)
                        page.wait_for_timeout(600)
                        print(f"  (clicked {label!r})")
                        break
                except Exception:
                    pass

            best = page.evaluate(LONGEST, list(NOISE))
            print(f"\n#{row['id']} {row['title'][:46]}")
            if best:
                parts = best["cls"].split()
                prefix = (re.sub(r"___[A-Za-z0-9_-]+$", "", parts[0]) if parts
                          else f"(no class, <{best['tag']}>)")
                found.append(prefix)
                print(f"  {best['len']:>4} chars  <{best['tag']} class={prefix}>")
                print(f"  {best['text'][:200]!r}")
            else:
                print("  no substantial text block found")

        if found:
            from collections import Counter
            print("\n── description container, by frequency ──")
            for cls, n in Counter(found).most_common():
                print(f"  {n}/{len(rows)}  [class*='{cls}']")
        b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""List the CSS-module component names on a detail page.

Sites built with CSS modules emit classes like `SummarySection__title___2rfVV`.
The trailing hash changes on every deploy, so a selector must match the stable
prefix: [class*='SummarySection__title']. This prints the prefixes and the text
under each so fields can be mapped to durable selectors.

    docker compose run --rm crawler python /app/scripts/probe_components.py
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict

from playwright.sync_api import sync_playwright

sys.path.insert(0, "/app")
from crawler import storage  # noqa: E402

DUMP = """
() => {
  const out = [];
  document.querySelectorAll('[class]').forEach(el => {
    const own = Array.from(el.childNodes)
      .filter(n => n.nodeType === 3).map(n => n.textContent).join(' ').trim();
    const all = (el.innerText || '').trim();
    Array.from(el.classList).forEach(c => {
      out.push({cls: c, own: own.slice(0, 120), all: all.slice(0, 160),
                tag: el.tagName.toLowerCase()});
    });
  });
  return out;
}
"""


def main() -> int:
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT l.url FROM listings l WHERE l.url <> '' ORDER BY l.id LIMIT 3"
        ).fetchall()
    if not rows:
        print("no listings stored")
        return 1

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        ctx = b.new_context(locale="hr-HR", timezone_id="Europe/Zagreb",
                            viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        for row in rows[:1]:
            page.goto(row["url"], wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_load_state("networkidle", timeout=25_000)
            except Exception:
                pass
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(2500)

            groups = defaultdict(list)
            for item in page.evaluate(DUMP):
                # Strip the CSS-module build hash: Name__part___HASH -> Name__part
                prefix = re.sub(r"___[A-Za-z0-9_-]+$", "", item["cls"])
                if prefix == item["cls"] and not re.search(r"__", prefix):
                    continue          # plain utility class, not a component
                groups[prefix].append(item)

            print(f"page: {row['url'][-70:]}")
            print(f"{len(groups)} component class prefixes\n")

            INTERESTING = ("descr", "opis", "text", "content", "body", "spec",
                           "detail", "attribute", "character", "karakter",
                           "seller", "agency", "agent", "advertiser", "contact",
                           "gallery", "image", "photo", "summary", "price")
            for prefix in sorted(groups):
                if not any(k in prefix.lower() for k in INTERESTING):
                    continue
                items = groups[prefix]
                sample = max(items, key=lambda x: len(x["all"]))
                if not sample["all"]:
                    continue
                print(f"[class*='{prefix}']  ({len(items)}x, {sample['tag']})")
                print(f"    {sample['all'][:150]!r}")

            print("\n── the longest text block on the page ──")
            best = max(page.evaluate(DUMP), key=lambda x: len(x["own"]))
            print(f"  class: {best['cls']}")
            print(f"  text : {best['own'][:300]!r}")

        b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

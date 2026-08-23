"""Find a stable selector for the description, and whether expanding is needed.

The text lives in a class-less <div>, so the selector has to hang off an
ancestor. Also checks whether the full text is already in the DOM (visually
clamped) or only appears after clicking a "show more" control — a truncated
description would silently poison text-similarity dedup.

    docker compose run --rm crawler python /app/scripts/probe_desc_anchor.py
"""
from __future__ import annotations

import re
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, "/app")
from crawler import storage  # noqa: E402

FIND = """
() => {
  const SKIP = new Set(['STYLE','SCRIPT','NOSCRIPT','SVG','PATH','HEAD','TITLE']);
  const NOISE = ['tipssection','similarad','pricecomparison','footer','header',
                 'nav','banner','sellersection','sellerinfo','cookie','didomi'];
  let best = null;
  document.querySelectorAll('div,p,section,article').forEach(el => {
    if (SKIP.has(el.tagName)) return;
    const cls = (typeof el.className === 'string' ? el.className : '').toLowerCase();
    if (NOISE.some(n => cls.includes(n))) return;
    const own = Array.from(el.childNodes)
      .filter(n => n.nodeType === 3).map(n => n.textContent).join(' ').trim();
    if (own.length < 100) return;
    if (!best || own.length > best.len) best = {el, len: own.length};
  });
  if (!best) return null;
  const chain = [];
  let node = best.el;
  for (let i = 0; i < 6 && node; i++) {
    const cls = typeof node.className === 'string' ? node.className : '';
    chain.push({tag: node.tagName.toLowerCase(), cls: cls.trim(),
                kids: node.children.length});
    node = node.parentElement;
  }
  return {len: best.len, text: best.el.innerText.slice(0, 120), chain};
}
"""


def main() -> int:
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT l.id, l.url FROM listings l WHERE l.url <> '' "
            "ORDER BY l.id LIMIT 3").fetchall()

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        ctx = b.new_context(locale="hr-HR", timezone_id="Europe/Zagreb",
                            viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        for row in rows:
            page.goto(row["url"], wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(1500)

            before = page.evaluate(FIND)
            len_before = before["len"] if before else 0

            clicked = False
            for label in ("Više", "Prikaži više", "Opširnije"):
                try:
                    btn = page.get_by_text(label, exact=True).first
                    if btn and btn.is_visible():
                        btn.click(timeout=2000)
                        page.wait_for_timeout(700)
                        clicked = True
                        break
                except Exception:
                    pass

            after = page.evaluate(FIND)
            len_after = after["len"] if after else 0

            print(f"\n#{row['id']}  before={len_before} chars, "
                  f"after_click={len_after} chars, clicked={clicked}")
            if len_after > len_before:
                print("  => EXPANDING IS REQUIRED (text was truncated)")
            elif len_before:
                print("  => full text already in the DOM (no click needed)")

            target = after or before
            if target:
                print("  ancestor chain (innermost first):")
                for i, node in enumerate(target["chain"]):
                    prefix = re.sub(r"___[A-Za-z0-9_-]+$", "", node["cls"].split()[0]) \
                        if node["cls"] else "(no class)"
                    print(f"    {i}: <{node['tag']}> {prefix}  ({node['kids']} children)")
        b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Dump candidate selectors for detail-page fields, using a real stored listing.

The list API gives price/area/location but not description, rooms, floor or
seller — this finds where those live in the rendered page.

    docker compose run --rm crawler python /app/scripts/probe_detail_fields.py [source-key]
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, "/app")
from crawler import storage  # noqa: E402

# For a given text fragment, report every selector that would match its element.
LOCATE = """
(needle) => {
  const out = [];
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
  while (walk.nextNode()) {
    const el = walk.currentNode;
    const own = Array.from(el.childNodes)
      .filter(n => n.nodeType === 3).map(n => n.textContent).join(' ').trim();
    if (!own || !own.includes(needle)) continue;
    const cls = Array.from(el.classList).filter(c => !/^(is-|js-)/.test(c));
    out.push({
      tag: el.tagName.toLowerCase(),
      cls: cls.slice(0, 4),
      id: el.id || null,
      attrs: Array.from(el.attributes).filter(a => a.name.startsWith('data-'))
                  .map(a => a.name + '=' + a.value).slice(0, 3),
      text: own.slice(0, 70),
      parentCls: Array.from(el.parentElement ? el.parentElement.classList : []).slice(0, 3),
    });
    if (out.length > 4) break;
  }
  return out;
}
"""


def show(page, label: str, needle: str) -> None:
    if not needle:
        print(f"  {label:<14} (nothing to look for)")
        return
    hits = page.evaluate(LOCATE, needle[:40])
    if not hits:
        print(f"  {label:<14} NOT FOUND for {needle[:40]!r}")
        return
    for h in hits[:2]:
        sel = h["tag"]
        if h["cls"]:
            sel += "." + ".".join(h["cls"])
        elif h["attrs"]:
            sel += f"[{h['attrs'][0]}]"
        print(f"  {label:<14} {sel}")
        print(f"  {'':<14}   parent: {h['parentCls']}  text: {h['text']!r}")


def main() -> int:
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT l.url, o.title, o.price_eur, o.area_m2 "
            "FROM listings l JOIN v_current o ON o.listing_id=l.id "
            "WHERE l.url <> '' ORDER BY l.id LIMIT 1").fetchone()
    if not row:
        print("no listings stored yet")
        return 1
    print(f"detail page: {row['url'][:120]}\n")

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        ctx = b.new_context(locale="hr-HR", timezone_id="Europe/Zagreb",
                            viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        r = page.goto(row["url"], wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_load_state("networkidle", timeout=25_000)
        except Exception:
            pass
        page.mouse.wheel(0, 1500)
        page.wait_for_timeout(2500)
        print(f"status={r.status if r else '?'} title={page.title()[:70]!r}\n")

        text = page.eval_on_selector("body", "e => e.innerText") or ""
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

        print("── where known values live ──")
        show(page, "title", row["title"])
        if row["price_eur"]:
            show(page, "price", f"{int(row['price_eur']):,}".replace(",", "."))

        # The description is the longest paragraph on the page.
        longest = max(lines, key=len) if lines else ""
        print(f"\n── longest paragraph ({len(longest)} chars) ──")
        print(f"  {longest[:180]}…")
        show(page, "description", longest[:40])

        print("\n── labelled spec rows (label followed by value) ──")
        LABELS = ("Broj soba", "Stambena površina", "Površina okućnice", "Kat",
                  "Godina izgradnje", "Stanje", "Grijanje", "Tip", "Broj etaža")
        for i, ln in enumerate(lines):
            for lab in LABELS:
                if ln.strip().lower().startswith(lab.lower()) and len(ln) < 40:
                    val = lines[i + 1] if i + 1 < len(lines) else ""
                    print(f"  {lab:<20} → {val[:40]!r}")
                    if lab == "Broj soba":
                        show(page, "  rooms-el", val[:20])
                    break

        print("\n── seller block ──")
        for probe in ("Agencija", "Oglašivač", "Agent", "ID KOD AGENCIJE"):
            idx = next((i for i, ln in enumerate(lines) if probe.lower() in ln.lower()), None)
            if idx is not None:
                print(f"  {probe:<18} {lines[idx][:50]!r} → next: {lines[idx+1][:40]!r}"
                      if idx + 1 < len(lines) else f"  {probe}: {lines[idx][:50]!r}")

        print("\n── container classes worth trying ──")
        for sel in ("[class*='escription']", "[class*='opis']", "[class*='detail']",
                    "[class*='spec']", "[class*='attribute']", "[class*='seller']",
                    "[class*='agency']", "dl", "table"):
            n = len(page.query_selector_all(sel))
            if n:
                el = page.query_selector(sel)
                t = (el.inner_text() or "").replace("\n", " | ")[:90] if el else ""
                print(f"  {sel:<26} {n:>3}x  {t}")

        b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

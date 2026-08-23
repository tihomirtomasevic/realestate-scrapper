"""Turn a parsed HTML page plus a FieldSpec into typed values.

Kept separate from the browser so it can be unit-tested against saved HTML with
no network and no Playwright.
"""
from __future__ import annotations

import re

from selectolax.parser import HTMLParser

from .source_config import FieldSpec


def _raw(node, spec: FieldSpec) -> str | None:
    if node is None:
        return None
    val = node.attributes.get(spec.attr) if spec.attr else node.text(strip=True)
    if val is None:
        return None
    val = val.strip()
    if spec.regex:
        m = re.search(spec.regex, val)
        if not m:
            return None
        val = m.group(1) if m.groups() else m.group(0)
    return val or None


def text_lines(tree: HTMLParser) -> list[str]:
    """Visible text, one entry per line, mirroring what a reader sees."""
    body = tree.css_first("body") or tree
    raw = body.text(separator="\n", strip=True)
    return [ln.strip() for ln in raw.split("\n") if ln.strip()]


def by_label(lines: list[str], label: str) -> str | None:
    """Value that follows a label line, as spec tables are rendered."""
    target = label.strip().lower().rstrip(":")
    for i, ln in enumerate(lines):
        cur = ln.strip().lower().rstrip(":")
        if cur == target and i + 1 < len(lines):
            return lines[i + 1].strip()
        # "Broj soba: 3" on a single line
        if cur.startswith(target + ":"):
            return ln.split(":", 1)[1].strip()
    return None


def text(tree: HTMLParser, spec: FieldSpec, lines: list[str] | None = None) -> str | None:
    if spec.label:
        val = by_label(lines if lines is not None else text_lines(tree), spec.label)
        if val and spec.regex:
            m = re.search(spec.regex, val)
            return (m.group(1) if m.groups() else m.group(0)) if m else None
        return val
    if spec.one_of:
        for option in spec.one_of:
            if tree.css_first(option["selector"]):
                return option.get("value")
        return None
    if not spec.selector:
        return None

    nodes = tree.css(spec.selector)
    if not nodes:
        return None
    if spec.pick == "longest":
        node = max(nodes, key=lambda n: len(n.text(strip=True) or ""))
    else:
        node = nodes[0]

    val = _raw(node, spec)
    if val and spec.first_line:
        # Re-read with line breaks so the first rendered line can be isolated.
        block = node.text(separator="\n", strip=True)
        val = next((ln.strip() for ln in block.split("\n") if ln.strip()), val)
    return val


def number(tree: HTMLParser, spec: FieldSpec, lines: list[str] | None = None) -> float | None:
    """Parse with the configured decimal convention.

    Croatian ads write '189.500,50' (dots group, comma decides). Getting this
    backwards silently turns 189500.50 into 189.50, so it is explicit config.
    """
    val = text(tree, spec, lines)
    if val is None:
        return None
    val = re.sub(r"[^\d.,-]", "", val)
    if not val:
        return None
    if spec.decimal == "comma":
        val = val.replace(".", "").replace(",", ".")
    else:
        val = val.replace(",", "")
    try:
        return float(val)
    except ValueError:
        return None


def many(tree: HTMLParser, spec: FieldSpec, base_url: str = "") -> list[str]:
    if not spec.selector:
        return []
    out: list[str] = []
    for node in tree.css(spec.selector):
        val = _raw(node, spec)
        if not val:
            continue
        out.append(absolutize(val, base_url) if base_url else val)
        if spec.max and len(out) >= spec.max:
            break
    return out


def absolutize(href: str, base_url: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base_url + "/", href)


def extract_detail(html: str, cfg, url: str) -> dict:
    """Apply a source's detail selectors to one ad page."""
    tree = HTMLParser(html)
    d = cfg.detail
    get = lambda k: d.get(k) or FieldSpec()  # noqa: E731
    lines = text_lines(tree)      # computed once; label lookups reuse it

    return {
        "url": url,
        "title": text(tree, get("title"), lines),
        "description": text(tree, get("description"), lines),
        "price_eur": number(tree, get("price"), lines),
        "area_m2": number(tree, get("area_m2"), lines),
        "rooms": number(tree, get("rooms"), lines),
        "floor": text(tree, get("floor"), lines),
        "location_raw": text(tree, get("location"), lines),
        "seller_name": text(tree, get("seller_name"), lines),
        "seller_phone": text(tree, get("seller_phone"), lines),
        "seller_type": text(tree, get("seller_type"), lines),
        "images": many(tree, get("images"), cfg.base_url) if "images" in d else [],
    }


def extract_list(html: str, cfg) -> list[dict]:
    """Find ad links on a search-results page."""
    tree = HTMLParser(html)
    out = []
    for item in tree.css(cfg.list_item):
        # "self" means the matched element IS the link — common when the only
        # reliable anchor for a result card is the per-ad <a> itself.
        link = item if cfg.list_link == "self" else item.css_first(cfg.list_link)
        href = link.attributes.get("href") if link else None
        if not href:
            continue
        url = absolutize(href, cfg.base_url)
        source_id = (item.attributes.get(cfg.source_id_attr) if cfg.source_id_attr else None)
        if not source_id:
            # Fall back to the last numeric-ish path segment of the detail URL.
            segments = [s for s in url.split("?")[0].rstrip("/").split("/") if s]
            source_id = segments[-1] if segments else url
        out.append({"source_id": str(source_id), "url": url})
    return out

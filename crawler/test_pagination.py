"""Pagination URL-building tests. No browser or network needed.

Run: python3 -m crawler.test_pagination
"""
import json
from urllib.parse import parse_qs, unquote, urlparse

try:
    from .pagination import page_url
    from .source_config import SourceConfig
except ImportError:
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from crawler.pagination import page_url
    from crawler.source_config import SourceConfig


def cfg(**pagination):
    return SourceConfig(key="t", name="t", base_url="https://e.test", searches=[],
                        list_item=".a", list_link="a", detail={}, pagination=pagination)


def decode(url: str, param: str, depth: int) -> dict:
    raw = parse_qs(urlparse(url).query)[param][0]
    for _ in range(depth):
        raw = unquote(raw)
    return json.loads(raw)


def main() -> int:
    failures = []

    def check(name, got, want):
        ok = got == want
        if not ok:
            failures.append(name)
        print(f"  [{'ok  ' if ok else 'FAIL'}] {name}")
        if not ok:
            print(f"         got  {got}\n         want {want}")

    print("query_param:")
    c = cfg(mode="query_param", param="page")
    check("page 1 untouched", page_url(c, "https://e.test/s?cat=x", 1), "https://e.test/s?cat=x")
    check("page 3 appends", page_url(c, "https://e.test/s?cat=x", 3),
          "https://e.test/s?cat=x&page=3")
    check("existing page replaced", page_url(c, "https://e.test/s?page=1&cat=x", 4),
          "https://e.test/s?page=4&cat=x")

    print("path_suffix:")
    c = cfg(mode="path_suffix")
    check("appends segment", page_url(c, "https://e.test/s/", 2), "https://e.test/s/2")

    print("start_at offset:")
    c = cfg(mode="query_param", param="p", start_at=0)
    check("0-based paging", page_url(c, "https://e.test/s", 2), "https://e.test/s?p=1")

    # The real-world case: the whole query is one double-URL-encoded JSON blob.
    print("json_param (double-encoded):")
    c = cfg(mode="json_param", param="q", json_page_key="page", double_encoded=True)
    blob = {"category": "houses", "ids": ["abc-123"], "priceTo": "300000",
            "page": 1, "sortOption": 4, "flag": False}
    from urllib.parse import quote
    enc = quote(quote(json.dumps(blob, separators=(",", ":")), safe=""), safe="")
    url = f"https://e.test/pretraga?q={enc}"

    check("page 1 returns the URL unchanged", page_url(c, url, 1), url)
    for n in (2, 5):
        out = decode(page_url(c, url, n), "q", 2)
        check(f"page {n} sets page key", out["page"], n)
        check(f"page {n} preserves priceTo", out["priceTo"], "300000")
        check(f"page {n} preserves array filter", out["ids"], ["abc-123"])
        check(f"page {n} preserves false-y value", out["flag"], False)

    print("json_param (single-encoded):")
    c1 = cfg(mode="json_param", param="q", double_encoded=False)
    enc1 = quote(json.dumps(blob, separators=(",", ":")), safe="")
    check("page 2 sets page key",
          decode(page_url(c1, f"https://e.test/s?q={enc1}", 2), "q", 1)["page"], 2)

    print("malformed input degrades safely:")
    check("missing param returns original",
          page_url(c, "https://e.test/s?other=1", 2), "https://e.test/s?other=1")
    check("non-JSON param returns original",
          page_url(c, "https://e.test/s?q=notjson", 2), "https://e.test/s?q=notjson")

    print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

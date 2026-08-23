"""Loader and validator for source definitions.

The engine has NO built-in site knowledge. Domains, selectors, pagination rules
and search URLs all come from gitignored YAML under config/sources/, so nothing
site-specific ever reaches the repository.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .json_api import ApiConfig

CONFIG_DIR = Path(os.getenv("SOURCES_DIR", "config/sources"))


class ConfigError(ValueError):
    """Raised with a path-qualified message so a typo is easy to locate."""


@dataclass(frozen=True)
class FieldSpec:
    """How to pull one field off a detail page."""
    selector: str | None = None
    # Spec tables render as a label line followed by its value ("Broj soba" /
    # "3") in markup with no useful classes, so matching on the visible label is
    # sturdier than any structural selector.
    label: str | None = None
    attr: str | None = None          # read an attribute instead of text
    regex: str | None = None         # keep capture group 1
    decimal: str = "comma"           # 'comma' (189.500,50) | 'dot' (189,500.50)
    multiple: bool = False
    max: int | None = None
    one_of: list[dict] = field(default_factory=list)
    # A generic container class often matches several sections; "longest"
    # picks the one with the most text, which is what a description is.
    pick: str = "first"          # first | longest
    # Concatenated blocks ("AgencyNameno ratingAll ads by…") are rarely wanted
    # whole; keep only the first rendered line.
    first_line: bool = False

    @staticmethod
    def parse(raw, where: str) -> "FieldSpec":
        if raw is None:
            return FieldSpec()
        if isinstance(raw, str):        # shorthand: bare selector string
            return FieldSpec(selector=raw)
        if not isinstance(raw, dict):
            raise ConfigError(f"{where}: expected a mapping or selector string")
        if raw.get("decimal", "comma") not in ("comma", "dot"):
            raise ConfigError(f"{where}.decimal: must be 'comma' or 'dot'")
        if raw.get("pick", "first") not in ("first", "longest"):
            raise ConfigError(f"{where}.pick: must be 'first' or 'longest'")
        if not raw.get("selector") and not raw.get("one_of") and not raw.get("label"):
            raise ConfigError(f"{where}: needs one of 'selector', 'label' or 'one_of'")
        return FieldSpec(
            selector=raw.get("selector"), label=raw.get("label"), attr=raw.get("attr"),
            regex=raw.get("regex"), decimal=raw.get("decimal", "comma"),
            multiple=bool(raw.get("multiple")), max=raw.get("max"),
            one_of=raw.get("one_of") or [],
            pick=raw.get("pick", "first"), first_line=bool(raw.get("first_line")),
        )


@dataclass(frozen=True)
class GoneSpec:
    """How to tell that an ad has stopped running.

    Absence from a search proves nothing on its own — promoted blocks rotate,
    a page can fail to load, a filter can wobble. This block says what to read
    back off the ad itself before believing it is over, and every marker in it
    is site-specific, so like everything else that identifies a target it lives
    only in the gitignored config.
    """
    # Cheapest check available: a per-ad JSON probe, no page render. The site's
    # own list endpoint often answers for a single id.
    api_probe_url: str | None = None        # may contain {source_id}
    api_count_path: str | None = None       # dotted path to a record count
    # Otherwise read the ad's page.
    expired_selector: str | None = None     # present only once the ad is over
    expired_text: list[str] = field(default_factory=list)
    alive_selector: str | None = None       # present only while it is running
    # HTTP codes that mean the ad was taken down rather than merely finished.
    # 410 Gone is the literal one, but sites differ on how long they serve it
    # before falling back to a plain 404.
    deleted_on_status: tuple[int, ...] = (404, 410)
    reason_when_absent: str = "not_listed"
    # An ad can drop out of one search and come back in the next, so leave a
    # gap before re-reading a page we have already judged.
    recheck_after_hours: float = 6.0
    max_checks_per_run: int = 40

    @staticmethod
    def parse(raw, where: str) -> "GoneSpec | None":
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ConfigError(f"{where}: expected a mapping")
        probe = raw.get("api_probe") or {}
        if probe and not probe.get("url"):
            raise ConfigError(f"{where}.api_probe.url: required")
        spec = GoneSpec(
            api_probe_url=probe.get("url"),
            api_count_path=probe.get("count_path"),
            expired_selector=raw.get("expired_selector"),
            expired_text=raw.get("expired_text") or [],
            alive_selector=raw.get("alive_selector"),
            deleted_on_status=tuple(
                int(c) for c in raw.get("deleted_on_status", (404, 410))),
            reason_when_absent=raw.get("reason_when_absent", "not_listed"),
            recheck_after_hours=float(raw.get("recheck_after_hours", 6)),
            max_checks_per_run=int(raw.get("max_checks_per_run", 40)),
        )
        if not (spec.api_probe_url or spec.expired_selector or spec.expired_text
                or spec.deleted_on_status):
            raise ConfigError(
                f"{where}: needs at least one of 'api_probe', 'expired_selector', "
                f"'expired_text' or 'deleted_on_status' — otherwise nothing can "
                f"confirm an ad is gone")
        if spec.reason_when_absent not in ("not_listed", "expired", "deleted"):
            raise ConfigError(
                f"{where}.reason_when_absent: must be not_listed, expired or deleted")
        return spec


@dataclass(frozen=True)
class Search:
    label: str
    url: str


@dataclass(frozen=True)
class SourceConfig:
    key: str
    name: str
    base_url: str
    searches: list[Search]
    list_item: str
    list_link: str
    detail: dict[str, FieldSpec]
    category: str | None = None
    source_id_attr: str | None = None
    pagination: dict = field(default_factory=dict)
    delay_min: float = 4.0
    delay_max: float = 11.0
    max_pages: int = 3
    max_details: int = 40
    respect_robots: bool = True
    ready_selector: str | None = None
    wait_for_idle: bool = True
    # Sites that fingerprint the browser need a real headed one (the container
    # provides a virtual display); leave true where headless works.
    headless: bool = True
    boilerplate: list[str] = field(default_factory=list)
    # When present, the list phase calls this JSON endpoint instead of parsing
    # search-results HTML. Detail pages may still be fetched for the fields the
    # API does not expose.
    api: "ApiConfig | None" = None
    # How to confirm an ad has stopped running. Without it the crawler can only
    # note that an ad went missing, never that it ended.
    gone: "GoneSpec | None" = None

    @property
    def host(self) -> str:
        from urllib.parse import urlparse
        return urlparse(self.base_url).netloc


_DETAIL_FIELDS = (
    "title", "description", "price", "area_m2", "rooms", "floor",
    "location", "seller_name", "seller_phone", "seller_type", "images",
)


def parse(raw: dict, where: str) -> SourceConfig:
    for required in ("key", "base_url", "searches", "detail"):
        if required not in raw:
            raise ConfigError(f"{where}: missing required key '{required}'")

    api_raw = raw.get("api")
    try:
        api_cfg = ApiConfig.parse(api_raw, where)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None

    lst = raw.get("list") or {}
    if not api_cfg and (not lst.get("item") or not lst.get("link")):
        raise ConfigError(
            f"{where}.list: both 'item' and 'link' are required "
            f"(unless an 'api:' block drives the list phase)")

    searches = []
    for i, s in enumerate(raw["searches"] or []):
        if not isinstance(s, dict) or "url" not in s:
            raise ConfigError(f"{where}.searches[{i}]: needs a 'url'")
        searches.append(Search(label=s.get("label") or f"search-{i + 1}", url=s["url"]))
    if not searches:
        raise ConfigError(f"{where}.searches: define at least one search")

    detail_raw = raw["detail"] or {}
    if not detail_raw.get("title"):
        raise ConfigError(f"{where}.detail.title: required (nothing else identifies an ad)")
    unknown = set(detail_raw) - set(_DETAIL_FIELDS)
    if unknown:
        raise ConfigError(
            f"{where}.detail: unknown field(s) {sorted(unknown)}; "
            f"supported: {sorted(_DETAIL_FIELDS)}")
    detail = {k: FieldSpec.parse(v, f"{where}.detail.{k}") for k, v in detail_raw.items()}

    crawl = raw.get("crawl") or {}
    delay = crawl.get("delay_seconds") or {}
    pagination = raw.get("pagination") or {}
    _MODES = ("query_param", "path_suffix", "next_link", "json_param")
    if pagination and pagination.get("mode") not in (None, *_MODES):
        raise ConfigError(
            f"{where}.pagination.mode: must be one of {', '.join(_MODES)}")
    if pagination.get("mode") == "json_param" and not pagination.get("param"):
        raise ConfigError(
            f"{where}.pagination.param: required for json_param mode "
            f"(the query parameter that holds the JSON)")

    return SourceConfig(
        key=raw["key"], name=raw.get("name") or raw["key"], base_url=raw["base_url"].rstrip("/"),
        category=raw.get("category"), searches=searches,
        list_item=lst.get("item", ""), list_link=lst.get("link", ""),
        source_id_attr=lst.get("source_id_attr"), api=api_cfg,
        detail=detail, pagination=pagination,
        gone=GoneSpec.parse(raw.get("gone"), f"{where}.gone"),
        delay_min=float(delay.get("min", 4)), delay_max=float(delay.get("max", 11)),
        max_pages=int(crawl.get("max_pages_per_search", 3)),
        max_details=int(crawl.get("max_details_per_run", 40)),
        respect_robots=bool(crawl.get("respect_robots", True)),
        ready_selector=crawl.get("ready_selector"),
        wait_for_idle=bool(crawl.get("wait_for_idle", True)),
        headless=bool(crawl.get("headless", True)),
        boilerplate=raw.get("boilerplate") or [],
    )


def load_all(selector: str | None = None) -> list[SourceConfig]:
    """Load configs named in CRAWLER_SOURCES ('*' or comma-separated basenames)."""
    selector = selector if selector is not None else os.getenv("CRAWLER_SOURCES", "*")
    if not CONFIG_DIR.is_dir():
        raise ConfigError(
            f"{CONFIG_DIR} does not exist. Copy config/sources/example.yaml.template "
            f"to config/sources/<name>.yaml and edit it — see config/sources/README.md")

    paths = sorted(p for p in CONFIG_DIR.glob("*.y*ml") if not p.name.endswith(".template"))
    if selector != "*":
        wanted = {n.strip() for n in selector.split(",") if n.strip()}
        paths = [p for p in paths if p.stem in wanted]
        missing = wanted - {p.stem for p in paths}
        if missing:
            raise ConfigError(f"CRAWLER_SOURCES names unknown source(s): {sorted(missing)}")

    if not paths:
        raise ConfigError(
            f"No source definitions found in {CONFIG_DIR}. This directory is "
            f"gitignored by design — see config/sources/README.md to create one.")

    configs = [parse(yaml.safe_load(p.read_text()) or {}, p.name) for p in paths]
    seen: set[str] = set()
    for c in configs:
        if c.key in seen:
            raise ConfigError(f"duplicate source key '{c.key}' across config files")
        seen.add(c.key)
    return configs

# Source definitions

Everything site-specific lives here: **domains, selectors, pagination rules and
the narrowed search URLs you actually want to track.**

Everything in this directory is **gitignored** except this README and
`example.yaml.template`. Nothing that names a real site reaches the repository —
the crawler in `crawler/` is a generic engine with no site knowledge compiled in.

## Getting started

```bash
cp config/sources/example.yaml.template config/sources/my-site.yaml
$EDITOR config/sources/my-site.yaml     # gitignored
```

Then either list it in `CRAWLER_SOURCES` in `.env`, or leave `CRAWLER_SOURCES=*`
to load every `.yaml` in this directory.

Validate what the engine parsed, without crawling:

```bash
docker compose run --rm crawler python -m crawler.run --validate
```

## Finding selectors

1. Open one of your saved searches in a browser, DevTools → Elements.
2. Right-click the element you want → Copy → Copy selector, then simplify it to
   something stable (prefer a `data-*` attribute or a semantic class over a long
   `div > div:nth-child(4)` chain, which breaks on any layout change).
3. `--validate` prints what each selector matched on a single fetched page, so
   you can iterate without running a full crawl.

Expect to revisit these every few months — a changed selector is the real
recurring maintenance cost of this project, not blocking.

## Safety notes

- **Keep the request rate low.** `crawl.delay_seconds` and
  `CRAWL_INTERVAL_MINUTES` are the entire anti-blocking strategy. A handful of
  searches polled every 20–30 minutes is below the noise floor of a real user.
- **Run it from a residential connection**, not a VPS. IP reputation is heavily
  weighted by bot detection, and a datacenter IP throws away that advantage.
- **Personal use only.** Don't redistribute scraped data or republish listings.
- Prefer a site's own saved-search email alerts where they exist — parsing those
  needs no crawling at all for discovery.

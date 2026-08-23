# adcrawler

Self-hosted classified-ad tracker. Crawls saved searches on ad sites into a local
Postgres database, groups duplicate ads into a single property, tracks price
history over time, and serves a search UI over it.

Built for personal use — watching a market and catching price drops early —
rather than for scale.

```
┌─────────┐   YAML source defs    ┌──────────┐        ┌────────────┐
│ crawler │ ◄──── (gitignored) ───│  config  │        │  React SPA │
│Playwright│                      └──────────┘        │   + nginx  │
└────┬────┘                                           └──────┬─────┘
     │ observations, images, blocking keys                   │ /api
     ▼                                                       ▼
┌──────────────────────────────┐                   ┌──────────────────┐
│ Postgres 16                  │ ◄──────────────── │ FastAPI          │
│ unaccent + pg_trgm FTS       │                   │ search / drops   │
└──────────────────────────────┘                   └──────────────────┘
```

## Why it is shaped this way

**Snapshots, not current state.** Every crawl appends an `observations` row, so
price history is a query rather than a schema migration.

**Clusters, not listings, are "a property".** A seller who deletes an ad and
reposts it cheaper creates a *new ad id*. At listing level that price drop is
invisible — each ad only ever had one price. Duplicate ads are therefore grouped
into a cluster, and all price reporting reads cluster-level history. This is the
single most important design decision in the project.

**Duplicate detection is evidence-based, not semantic.** "New build, 65 m², 2
rooms" describes twenty different flats in one building; embeddings score those
~0.97 similar. Near-duplicate *text* matching plus perceptual image hashes and
structured-field agreement separate a genuine repost from a neighbouring unit.
See [PLAN.md](PLAN.md) for the signal weights and the reasoning.

**Thresholds are biased toward splitting.** A false merge silently *hides* a
listing from you, which defeats the point. A false split just shows the same flat
twice. Uncertain pairs go to a review queue instead of being decided.

## Quick start

```bash
git clone <your-repo> adcrawler && cd adcrawler

cp .env.example .env
$EDITOR .env                       # set POSTGRES_PASSWORD at minimum

cp config/sources/example.yaml.template config/sources/my-site.yaml
$EDITOR config/sources/my-site.yaml   # add your target + saved searches

make up                            # db + api + web, waits until the API answers
open http://localhost:8080
```

`make` on its own lists every target. `make down` stops the app and keeps your
data; only `make nuke` deletes the database, and it asks first.

The crawler is opt-in so it never starts by surprise:

```bash
make validate      # check configs and selectors against one live page
make crawl         # a single pass, watched
make scheduler     # background loop, every CRAWL_INTERVAL_MINUTES

# maintenance passes over data already stored
make verify-gone   # settle whether vanished ads actually ended
make dedup
make phash
```

Every target is a thin wrapper over `docker compose` — `make -n up` prints the
command it would run, if you would rather type it yourself.

Postgres is published on host port **15432**, not 5432, to avoid colliding with
other local databases. Change `DB_PORT` in `.env` if you prefer another.

## Configuration and privacy

**No target site appears anywhere in this repository.** The crawler is a generic,
config-driven engine; domains, CSS selectors, pagination rules and the search URLs
you actually track all live in `config/sources/*.yaml`, which is gitignored. Only
a fictional `example.yaml.template` is committed. See
[config/sources/README.md](config/sources/README.md).

Gitignored by design: `.env`, `config/sources/*` (except the template), and any
local database dump — scraped rows contain real listing URLs.

`.env` covers credentials, ports, crawl rate, dedup thresholds and the optional
AI and email features. Everything has a working default except the DB password.

## API

Interactive docs at `http://localhost:8000/docs`.

| Endpoint | Purpose |
|---|---|
| `GET /api/listings` | Search: full-text `q`, price/area/rooms/location/seller filters, sorting, pagination, optional duplicate grouping |
| `GET /api/listings/{id}` | One ad: cluster-wide price history, images, AI verdict, sibling ads |
| `GET /api/price-drops` | Clusters whose price fell, with `min_drop_pct` and a time window |
| `GET /api/dedup/review` | Pairs awaiting a human decision |
| `POST /api/dedup/review/{id}` | Label a pair `same` / `different`; `same` merges the clusters |
| `GET /api/stats` | Counts for the header bar |
| `GET /api/health` | Liveness + DB reachability |

Full-text search is diacritic-insensitive in both directions — `tresnjevka`
matches `Trešnjevka` and vice versa — via a generated `tsvector` column over
`unaccent`, with `pg_trgm` for fuzzy title matching.

## Development

```bash
docker compose up -d db api           # backend only
cd web && npm install && npm run dev  # Vite dev server on :5173, proxying /api

make rebuild                          # after changing api/, web/ or crawler/
make test                             # dedup + pagination suites, no browser
make check                            # no target site leaked into tracked files
make logs S=crawler                   # follow one service

make psql                             # or, from the host:
psql -h localhost -p 15432 -U adcrawler -d adcrawler   # host port, avoids 5432
```

## Layout

```
api/          FastAPI service — raw SQL against hand-tuned views
crawler/      generic engine, source-config loader, dedup logic
  dedup.py      scoring logic (pure, unit-tested)
  dedup_pass.py blocking → scoring → clustering, against the database
  images.py     perceptual hashing of listing photos
  pagination.py page-N URL building (incl. JSON-in-query-param)
  extract.py    selector → typed value (comma-decimal aware)
config/       source definitions (gitignored except the template)
db/migrations Postgres schema, applied on first start of an empty volume
web/          React + Vite + TypeScript SPA, served by nginx
```

## Operating notes

Keep the request rate low and run from a residential connection: a handful of
searches polled every 20–30 minutes is below the noise floor of ordinary
browsing, and IP reputation weighs heavily in bot detection. `robots.txt` is
honoured by default (`crawl.respect_robots`). The realistic maintenance cost is
selectors breaking when a site changes its markup — `--validate` exists to make
that a two-minute fix.

Personal use. Don't redistribute scraped data or republish listings.

## Status

Working: schema, dedup engine wired into the crawl loop, image perceptual
hashing, API, web UI, Docker orchestration.
Not yet wired up: the boilerplate learner, LLM scoring, and the digest email.
The crawl engine has not yet been run against a live site — its extraction and
pagination layers are unit-tested, but selectors will need iteration via
`--validate`. See [PLAN.md](PLAN.md).

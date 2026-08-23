# adcrawler — plan

Personal-use crawler for classified real-estate ads: collect into a local DB,
track price history, dedupe, score with an LLM, email a digest of the good ones.

Target sites are deliberately not named here — they live only in gitignored
`config/sources/*.yaml`. See `config/sources/README.md`.

## Status

| Stage | State |
|---|---|
| Postgres schema (snapshots + clusters + dedup + FTS) | **done** — `db/migrations/001_init.sql`, verified against PG16 |
| Dedup logic (blocking, scoring, clustering) | **done** — `crawler/dedup.py`, 6/6 scenarios pass |
| Config-driven crawl engine | **done** — `crawler/`; no site details in the repo |
| REST API (search, drops, review, stats) | **done** — `api/`, verified end-to-end |
| React + Vite search UI | **done** — `web/` |
| Docker orchestration | **done** — `docker-compose.yml`, stack runs |
| Mobile-API recon (mitmproxy) | **user action** — 30 min, see below |
| Real source definitions (your searches) | **user action** — `config/sources/*.yaml` |
| Image pHash during crawl | **done** — `crawler/images.py`, runs after collection |
| Dedup pass wired into the crawl loop | **done** — `crawler/dedup_pass.py`, verified on seeded data |
| Boilerplate learner | after first real data |
| LLM extract + score | after real data |
| Digest email | last |

## Stack

Postgres 16 (`unaccent` + `pg_trgm` FTS) · FastAPI · React + Vite + TypeScript ·
nginx · Playwright. Four compose services: `db`, `api`, `web`, and an opt-in
`crawler` behind the `crawl` profile.

**Nothing site-specific is committed.** The crawler is a generic engine driven by
gitignored YAML in `config/sources/`; only a fictional template is tracked.
Verified: a config naming a real domain is ignored, and that domain appears in
zero tracked files.

## Design decisions

**Snapshots, not current state.** Every crawl appends to `observations`.
Price/area history is a query, never a migration.

**Clusters, not listings, are "a property".** A repost is a *new source ad id*,
so a price drop across a repost is invisible at listing level — the single most
valuable signal would be silently lost. Reports read `v_cluster_price_history`.

**Rate is the whole anti-blocking strategy.** ~50–200 requests/day from a Croatian
residential IP, with a persistent Playwright profile, is below the noise floor of
a real user. Do not run this on a VPS; do not redistribute scraped data.
Assume Playwright is the real path — a clean mobile API is upside, not the plan.

## Duplicate recognition

Four cases to handle: (1) seller reposts every ~30 days to bump the ad,
(2) several agencies list one property, (3) the same flat on both portals,
(4) — the trap — *different units in the same new building*.

**Why not embeddings as the driver.** "Novogradnja, Trešnjevka, 65m², 2s+db"
describes twenty distinct flats; embeddings score them ~0.97. Near-duplicate
*text* matching (MinHash over character 5-shingles) finds lightly-edited copies —
what a repost actually is — without collapsing distinct units. `embed_cosine` is
reserved in the schema as an optional tiebreak; not needed to start. No vector DB:
at this scale brute-force cosine is milliseconds, so pgvector solves a problem
we do not have.

**Errors are asymmetric.** A false merge silently *hides* a listing from you —
defeating the app's purpose. A false split just shows the same flat twice.
Thresholds are biased toward splitting (merge ≥ 0.75, review ≥ 0.45).

### Pipeline

1. **Blocking** (`blocking_keys` table) — candidates are a self-join on shared
   keys: `area_city`, `phash_band` (LSH over 64-bit hashes), `phone`, `text_band`
   (MinHash bands). Never an all-pairs comparison. Keys use `crc32`, never
   Python's per-process-salted `hash()`, so they stay valid once persisted.
2. **Pairwise scoring** (`dedup_pairs`) — signals stored as *columns*, so weights
   can be retuned against past pairs without recrawling.
3. **Decision** — `merge` / `review` / `distinct`. `human_label` on a reviewed
   pair is your training data for tuning the weights.
4. **Clustering** — union-find over `merge` edges only, with a runaway guard:
   A~B and B~C does not imply A~C, so any cluster past 5 members is `flagged`
   rather than trusted.

### Signal weights

| Signal | Weight | Note |
|---|---|---|
| ≥2 matching photos (Hamming ≤ 8) | +0.55 | strongest signal; agencies reuse the owner's photo set |
| exactly 1 matching photo | +0.25 | often just a shared building exterior |
| private-seller phone match | +0.45 | **agency** phone scores 0 — one switchboard, all listings |
| precise area match (e.g. 64.3) | +0.30 | round numbers (65) only +0.12 — weak fingerprint |
| text MinHash ≥ 0.80 | +0.35 | after boilerplate stripping |
| price within 3% | +0.08 | weak: real duplicates differ (agency fee in/out) |
| floor / rooms match | +0.05 | |
| **floor conflict** | **−0.45** | in one building, floor is *the* discriminator |
| **area conflict > 2%** | **−0.25** | contradiction is evidence against, not absent evidence |
| **rooms conflict** | **−0.20** | |
| **location conflict** | **GATE** | forces `distinct` regardless of everything else |

Penalties are not vetoes: strong interior-photo overlap can still outweigh a
floor conflict and push a pair to `review` (likely a typo worth a human look)
rather than discarding it.

### Gotchas encoded

- **Agency boilerplate poisons text similarity** — every ad from an agency carries
  the same footer, so unrelated flats from that agency look similar. Strip
  recurring per-seller snippets (`seller_boilerplate`) before comparing.
  Measured: jaccard 0.586 → 0.000 on two unrelated ads once stripped.
- **Croatian normalization** — `đ` does not decompose under NFKD (needs an
  explicit map); comma decimals (`65,50 m²`); floor words (`prizemlje`,
  `drugi kat`, `potkrovlje`).

## Next steps

1. **Write real source definitions** (yours): copy the template to
   `config/sources/<name>.yaml`, add your narrowed searches, then iterate with
   `docker compose run --rm crawler python -m crawler.run --validate` until every
   field reports `ok`. The easier target first — lighter protection means fewer
   variables while the pipeline is still unproven.
2. **Mobile-API recon** (yours, ~30 min, optional): mitmproxy + phone on the same
   wifi, open the app, watch for JSON on the site's hosts. Clean JSON → a much
   simpler HTTP client; cert pinning or Cloudflare → stay on Playwright. Treat a
   usable app API as upside, not the plan.
3. **Boilerplate learner** — mine recurring per-seller snippets from real ad text
   into `seller_boilerplate`.
4. **LLM layer** — extract structured fields (flag a "65 m²" that is gross,
   missing floor, vague location), then score against explicit criteria with the
   reasoning retained.
5. **Digest email** — new + price-dropped clusters, deduplicated.

## Known gaps

- Dedup weights are reasoned, not fitted, and all scenarios so far are synthetic.
  Expect to tune once real ads land — that is what `dedup_pairs.human_label` is
  for, and `--dedup-only` re-scores everything stored after a change.
- The crawl engine has not been run against a live site. Its extraction and
  pagination layers are unit-tested; selectors are the part that will need
  iteration via `--validate`.
- Boilerplate is only what you seed in a source config; nothing mines it from
  real ad text yet.
- Image hashing skips images under 120px and marks undecodable ones with an
  empty hash so they are not retried forever.

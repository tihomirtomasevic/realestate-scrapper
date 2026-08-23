-- adcrawler — Postgres schema
--
-- Two design principles:
--   1. SNAPSHOTS, not current state. Every crawl of an ad appends to
--      `observations`, so price/area history is a query, not a migration.
--   2. CLUSTERS, not listings, are the unit of "a property". A repost is a new
--      source ad id, so a price drop across a repost is INVISIBLE at listing
--      level. Reports read cluster-level history.

CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Full-text search must fold Croatian diacritics so "Trešnjevka" matches
-- "tresnjevka". A generated column requires an IMMUTABLE function, but
-- unaccent() is only STABLE (it depends on a loadable dictionary). The
-- two-argument form with an explicit dictionary IS immutable, so wrap it.
CREATE OR REPLACE FUNCTION immutable_unaccent(text)
RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS
$$ SELECT public.unaccent('public.unaccent', $1) $$;

-- ─────────────────────────────────────────────────────────────────────────
-- Clusters: one row per real-world property. Listings link into one.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE clusters (
    id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_listing_id BIGINT,                 -- best representative
    size                 INTEGER NOT NULL DEFAULT 1,
    status               TEXT NOT NULL DEFAULT 'auto'
        CHECK (status IN ('auto', 'confirmed', 'flagged')),
        -- 'flagged' = grew past the runaway guard; likely a bad transitive chain
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_clusters_status ON clusters (status, size);

-- ─────────────────────────────────────────────────────────────────────────
-- Identity: one row per distinct source ad.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE listings (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source     TEXT NOT NULL,          -- matches `key` in a source YAML
    source_id  TEXT NOT NULL,          -- the platform's native ad id
    url        TEXT NOT NULL,
    category   TEXT,
    cluster_id BIGINT REFERENCES clusters(id) ON DELETE SET NULL,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
    active     BOOLEAN NOT NULL DEFAULT true,
    removed_at TIMESTAMPTZ,
    UNIQUE (source, source_id)
);
CREATE INDEX idx_listings_cluster ON listings (cluster_id);
CREATE INDEX idx_listings_active  ON listings (active, last_seen DESC);

ALTER TABLE clusters
    ADD CONSTRAINT fk_clusters_canonical
    FOREIGN KEY (canonical_listing_id) REFERENCES listings(id) ON DELETE SET NULL;

-- ─────────────────────────────────────────────────────────────────────────
-- Observations: one row per crawl that saw this ad. THIS is the history.
-- raw_json is kept so improved extraction never requires a re-crawl.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE observations (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id    BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    captured_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    title         TEXT,
    description   TEXT,
    price_eur     NUMERIC(12, 2),      -- NULL = "na upit" / unpriced
    area_m2       NUMERIC(8, 2),       -- as advertised; may be gross (AI flags it)
    rooms         NUMERIC(4, 1),
    floor         TEXT,                -- raw: 'prizemlje', '3', 'potkrovlje'
    floor_num     INTEGER,             -- parsed; NULL for 'attic'/unknown
    location_raw  TEXT,
    location_norm TEXT,                -- diacritic-folded, gazetteer-mapped
    lat           DOUBLE PRECISION,
    lon           DOUBLE PRECISION,
    seller_type   TEXT CHECK (seller_type IN ('private', 'agency') OR seller_type IS NULL),
    seller_name   TEXT,
    seller_phone  TEXT,                -- normalized (385…)
    text_fp       TEXT,                -- MinHash signature, boilerplate stripped
    raw_json      JSONB,
    content_hash  TEXT NOT NULL,       -- skip write when nothing changed
    -- Diacritic-insensitive full-text vector. 'simple' (not a language config)
    -- because Postgres ships no Croatian stemmer; unaccent + trigram carry it.
    search_tsv    tsvector GENERATED ALWAYS AS (
        to_tsvector('simple',
            immutable_unaccent(coalesce(title, '') || ' ' || coalesce(description, '')))
    ) STORED,
    UNIQUE (listing_id, content_hash)
);
CREATE INDEX idx_obs_listing_time ON observations (listing_id, captured_at DESC);
CREATE INDEX idx_obs_price        ON observations (price_eur);
CREATE INDEX idx_obs_area         ON observations (area_m2);
CREATE INDEX idx_obs_phone        ON observations (seller_phone) WHERE seller_phone IS NOT NULL;
CREATE INDEX idx_obs_search       ON observations USING GIN (search_tsv);
CREATE INDEX idx_obs_title_trgm   ON observations USING GIN (title gin_trgm_ops);

-- ─────────────────────────────────────────────────────────────────────────
-- Images: perceptual hash is the strongest cross-listing signal — agencies
-- reuse the owner's photo set across reposts and portals.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE images (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    url        TEXT NOT NULL,
    phash      TEXT,                   -- 64-bit perceptual hash, hex
    position   INTEGER,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (listing_id, url)
);
CREATE INDEX idx_images_phash ON images (phash) WHERE phash IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────
-- DEDUP STAGE 1 — blocking keys. Candidate pairs are a self-join here;
-- never an all-pairs comparison. Keys use crc32 (stable across processes),
-- never Python's per-process-salted hash().
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE blocking_keys (
    listing_id BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    key_type   TEXT NOT NULL,   -- area_city | phash_band | phone | text_band
    key_value  TEXT NOT NULL,
    PRIMARY KEY (listing_id, key_type, key_value)
);
CREATE INDEX idx_blocking_lookup ON blocking_keys (key_type, key_value);

-- ─────────────────────────────────────────────────────────────────────────
-- DEDUP STAGE 2/3 — pairwise evidence and decision. Signals are columns, not
-- just JSON, so weights can be retuned against past pairs without recrawling.
-- human_label is the training data for that tuning.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE dedup_pairs (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_a         BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    listing_b         BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    score             REAL NOT NULL,
    decision          TEXT NOT NULL CHECK (decision IN ('merge', 'review', 'distinct')),
    img_match_count   INTEGER NOT NULL DEFAULT 0,
    area_delta        REAL,
    area_precise      BOOLEAN NOT NULL DEFAULT false,
    price_delta_pct   REAL,
    text_jaccard      REAL,
    embed_cosine      REAL,            -- reserved: optional tiebreak, unused by default
    phone_match       BOOLEAN NOT NULL DEFAULT false,
    same_seller       BOOLEAN NOT NULL DEFAULT false,
    floor_conflict    BOOLEAN NOT NULL DEFAULT false,
    location_conflict BOOLEAN NOT NULL DEFAULT false,  -- GATE: forces 'distinct'
    evidence_json     JSONB,
    human_label       TEXT CHECK (human_label IN ('same', 'different') OR human_label IS NULL),
    labeled_at        TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (listing_a < listing_b),     -- canonical ordering, no mirrored rows
    UNIQUE (listing_a, listing_b)
);
CREATE INDEX idx_pairs_decision ON dedup_pairs (decision, score DESC);
CREATE INDEX idx_pairs_review   ON dedup_pairs (score DESC)
    WHERE decision = 'review' AND human_label IS NULL;

-- ─────────────────────────────────────────────────────────────────────────
-- Per-seller boilerplate. Every agency ad carries the same footer, which
-- inflates text similarity between UNRELATED flats from that agency.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE seller_boilerplate (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source      TEXT NOT NULL,
    seller_name TEXT NOT NULL,
    snippet     TEXT NOT NULL,
    seen_count  INTEGER NOT NULL DEFAULT 1,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, seller_name, snippet)
);

-- ─────────────────────────────────────────────────────────────────────────
-- AI layer: extract structured fields, then score against explicit criteria,
-- keeping the reasoning so you can read *why* and tune over time.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE ai_analysis (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    observation_id BIGINT NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    model          TEXT NOT NULL,
    score          REAL,
    verdict        TEXT CHECK (verdict IN ('recommend', 'maybe', 'skip') OR verdict IS NULL),
    reasoning      TEXT,
    flags_json     JSONB,       -- ['area_maybe_gross','no_floor_stated', …]
    fields_json    JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    notified       BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX idx_ai_obs      ON ai_analysis (observation_id);
CREATE INDEX idx_ai_pending  ON ai_analysis (created_at DESC) WHERE notified = false;

-- ─────────────────────────────────────────────────────────────────────────
-- Crawl log: rate-limit sanity and which run produced what.
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE crawl_runs (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source       TEXT NOT NULL,
    search_label TEXT,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    pages        INTEGER NOT NULL DEFAULT 0,
    requests     INTEGER NOT NULL DEFAULT 0,
    new_ads      INTEGER NOT NULL DEFAULT 0,
    updated_ads  INTEGER NOT NULL DEFAULT 0,
    error        TEXT
);
CREATE INDEX idx_runs_started ON crawl_runs (started_at DESC);

-- ─────────────────────────────────────────────────────────────────────────
-- Views
-- ─────────────────────────────────────────────────────────────────────────

-- Latest observation per listing — the "current state" most queries want.
CREATE VIEW v_current AS
SELECT DISTINCT ON (o.listing_id) o.*
FROM observations o
ORDER BY o.listing_id, o.captured_at DESC;

-- Price history ACROSS a cluster: survives reposts under new ad ids.
CREATE VIEW v_cluster_price_history AS
SELECT
    l.cluster_id,
    l.id     AS listing_id,
    l.source,
    l.url,
    o.captured_at,
    o.price_eur,
    o.area_m2,
    CASE WHEN o.area_m2 > 0 THEN ROUND(o.price_eur / o.area_m2, 2) END AS price_per_m2
FROM listings l
JOIN observations o ON o.listing_id = l.id
WHERE l.cluster_id IS NOT NULL AND o.price_eur IS NOT NULL;

-- Cluster-level price movement. THIS is what the price-drop feed reads:
-- a repost at a lower price shows up here and nowhere else.
CREATE VIEW v_cluster_price_changes AS
SELECT
    cluster_id,
    COUNT(DISTINCT listing_id)                       AS listing_count,
    COUNT(DISTINCT price_eur)                        AS distinct_prices,
    MIN(captured_at)                                 AS first_seen,
    MAX(captured_at)                                 AS last_seen,
    (ARRAY_AGG(price_eur ORDER BY captured_at ASC))[1]  AS first_price,
    (ARRAY_AGG(price_eur ORDER BY captured_at DESC))[1] AS latest_price,
    MIN(price_eur)                                   AS min_price,
    MAX(price_eur)                                   AS max_price,
    ROUND(
        ((ARRAY_AGG(price_eur ORDER BY captured_at DESC))[1]
       - (ARRAY_AGG(price_eur ORDER BY captured_at ASC))[1])
        / NULLIF((ARRAY_AGG(price_eur ORDER BY captured_at ASC))[1], 0) * 100
    , 2)                                             AS change_pct
FROM v_cluster_price_history
GROUP BY cluster_id
HAVING COUNT(DISTINCT price_eur) > 1;

-- The review queue — pairs the scorer was not confident about.
CREATE VIEW v_review_queue AS
SELECT p.*, la.url AS url_a, lb.url AS url_b
FROM dedup_pairs p
JOIN listings la ON la.id = p.listing_a
JOIN listings lb ON lb.id = p.listing_b
WHERE p.decision = 'review' AND p.human_label IS NULL;

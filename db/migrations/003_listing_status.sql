-- ─────────────────────────────────────────────────────────────────────────
-- Why an ad stopped being advertised.
--
-- `active` only ever said "we did not see it in the last search", which is a
-- weaker claim than it looks: a promoted-block ad rotating off page 1 vanishes
-- from the results without anything happening to the ad. Every listing we had
-- flagged inactive on 2026-08-22 turned out to still be running.
--
-- So absence is now a suspicion (`missing_since`), and `status` only moves to
-- 'gone' once the ad's own page has been read back and says so.
--
-- Neither portal exposes a sold flag for real estate — an ad that sold and an
-- ad the seller gave up on look identical. We record what is observable and
-- leave the interpretation to the reader, which is why the view below carries
-- how long it ran and what the price did rather than a verdict.
-- ─────────────────────────────────────────────────────────────────────────

ALTER TABLE listings
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS status_reason TEXT,
    ADD COLUMN IF NOT EXISTS status_checked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS missing_since TIMESTAMPTZ;

ALTER TABLE listings DROP CONSTRAINT IF EXISTS listings_status_check;
ALTER TABLE listings ADD CONSTRAINT listings_status_check
    CHECK (status IN ('active', 'missing', 'gone', 'out_of_scope'));

-- status:
--   active        returned by our search on the last crawl
--   missing       absent from the search, not yet re-checked
--   gone          re-checked, and the site says the ad has ended
--   out_of_scope  re-checked and still running, but our search no longer
--                 returns it. Promoted placements land here: they were never
--                 ours to track, and they must not sit in the ended feed
--                 pretending a sale happened.
--
-- status_reason:
--   expired       the page says the ad is no longer running
--   deleted       the page is gone entirely (404)
--   not_listed    the API no longer returns it, and there is no page to read
--   still_running the ad is live, it just does not match our search
--   relisted      it came back: the same ad reappeared in a later search
COMMENT ON COLUMN listings.status_reason IS
    'expired | deleted | not_listed | still_running | relisted';

CREATE INDEX IF NOT EXISTS idx_listings_status
    ON listings (status, removed_at DESC);
CREATE INDEX IF NOT EXISTS idx_listings_missing
    ON listings (missing_since) WHERE status = 'missing';

-- Existing rows: `active=false` was an unverified guess, so it becomes a
-- suspicion to re-check rather than a confirmed disappearance.
UPDATE listings
   SET status = 'missing',
       missing_since = COALESCE(missing_since, removed_at, last_seen)
 WHERE NOT active AND status = 'active';

-- ─────────────────────────────────────────────────────────────────────────
-- The feed: ads that stopped running, with the evidence that says whether
-- the price was the reason.
--
-- Deliberately cluster-aware. A property re-advertised under a new ad id has
-- a dead listing and a live one; only the cluster knows the property itself is
-- still on the market, and showing it as "gone" would be exactly backwards.
-- ─────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_gone_listings AS
WITH latest AS (
    SELECT l.id AS listing_id, l.cluster_id, l.source, l.url, l.status,
           l.status_reason, l.first_seen, l.last_seen, l.removed_at,
           l.status_checked_at,
           o.title, o.description, o.price_eur, o.area_m2, o.rooms, o.floor,
           o.location_raw, o.seller_type, o.seller_name,
           CASE WHEN o.area_m2 > 0 THEN ROUND(o.price_eur / o.area_m2, 2)
           END AS price_per_m2
    FROM listings l
    JOIN v_current o ON o.listing_id = l.id
    WHERE l.status = 'gone'
),
prices AS (
    SELECT o.listing_id,
           (ARRAY_AGG(o.price_eur ORDER BY o.captured_at ASC))[1]  AS first_price,
           (ARRAY_AGG(o.price_eur ORDER BY o.captured_at DESC))[1] AS last_price
    FROM observations o
    WHERE o.price_eur IS NOT NULL
    GROUP BY o.listing_id
)
SELECT
    g.*,
    p.first_price,
    p.last_price,
    ROUND((p.last_price - p.first_price)
          / NULLIF(p.first_price, 0) * 100, 2)            AS price_change_pct,
    -- Days between first sighting and the last time it was still up. An ad we
    -- only ever saw once has 0, not NULL: it went quickly.
    GREATEST(0, EXTRACT(EPOCH FROM (
        COALESCE(g.removed_at, g.last_seen) - g.first_seen)) / 86400
    )::numeric(10,1)                                       AS days_listed,
    -- Asking price against everything else advertised in the same settlement.
    -- This is the "was it actually a good buy" column: a house that went fast
    -- and well under the local rate is the interesting one.
    med.median_price_per_m2,
    CASE WHEN med.median_price_per_m2 > 0 AND g.price_per_m2 > 0
         THEN ROUND((g.price_per_m2 - med.median_price_per_m2)
                    / med.median_price_per_m2 * 100, 1)
    END                                                    AS vs_area_median_pct,
    -- True when the property is still advertised elsewhere: this ad ended, the
    -- sale did not.
    EXISTS (SELECT 1 FROM listings s
             WHERE s.cluster_id = g.cluster_id
               AND g.cluster_id IS NOT NULL
               AND s.id <> g.listing_id
               AND s.status = 'active')                    AS still_listed_elsewhere
FROM latest g
LEFT JOIN prices p ON p.listing_id = g.listing_id
LEFT JOIN LATERAL (
    -- PERCENTILE_CONT returns double precision; everything else here is
    -- numeric, and mixing the two makes ROUND(x, 2) unresolvable.
    SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (
               ORDER BY CASE WHEN oo.area_m2 > 0
                             THEN oo.price_eur / oo.area_m2 END
           )::numeric AS median_price_per_m2
    FROM listings ll
    JOIN v_current oo ON oo.listing_id = ll.id
    WHERE oo.location_raw = g.location_raw AND oo.price_eur IS NOT NULL
) med ON TRUE;

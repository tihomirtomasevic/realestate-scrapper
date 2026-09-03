-- adcrawler — condition gate
--
-- Three property states are hard exclusions, not low scores: an unfinished
-- shell, a ruin, and a house sold "for adaptation". They are filtered out of
-- the listings feed by default and never reach the scorer.
--
-- Two fields beyond the booleans, both learned from real ads:
--
--   severity  A listing that "zahtijeva potpunu i temeljitu adaptaciju" and one
--             that "moze se koristiti uz manje uredenje" both set
--             needs_adaptation. The second says the house is usable as-is, so
--             gating it hides a viable property over a coat of paint.
--
--   scope     "potkrovlje koje nije uredeno" is an unfinished ATTIC, not an
--             unfinished house. Without scope it gates the same as a building
--             with no roof.

CREATE TABLE listing_gate (
    listing_id       BIGINT PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,

    -- Re-run only when the ad text actually changes. A price edit creates a new
    -- observation but does not change what the copy says about condition.
    text_hash        TEXT NOT NULL,
    model            TEXT NOT NULL,
    prompt_version   TEXT NOT NULL,

    unfinished       BOOLEAN NOT NULL DEFAULT false,
    ruin             BOOLEAN NOT NULL DEFAULT false,
    needs_adaptation BOOLEAN NOT NULL DEFAULT false,

    severity         TEXT CHECK (severity IN ('cosmetic', 'partial', 'full')),
    scope            TEXT CHECK (scope    IN ('whole', 'part')),

    -- Verbatim spans copied from the ad, one per set flag. A span that is not a
    -- literal substring of the ad is dropped before insert and its flag is
    -- cleared, so anything stored here can be shown to the user as the reason.
    evidence         JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- The filter reads this, so the policy lives in one place instead of being
    -- restated by every caller.
    gated BOOLEAN GENERATED ALWAYS AS (
        ruin
        OR (unfinished       AND scope    IS DISTINCT FROM 'part')
        OR (needs_adaptation AND severity IS DISTINCT FROM 'cosmetic')
    ) STORED,

    checked_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_gate_gated ON listing_gate (gated) WHERE gated;

-- Property-level verdict. Agencies describe the same house differently, so a
-- flag raised by ANY ad for a property gates the whole property: these are
-- factual states, and one agency omitting "roh bau" does not unbuild the house.
CREATE VIEW v_property_gate AS
SELECT
    COALESCE('c' || l.cluster_id, 'l' || l.id) AS group_key,
    bool_or(g.gated)                           AS gated,
    bool_or(g.unfinished)                      AS unfinished,
    bool_or(g.ruin)                            AS ruin,
    bool_or(g.needs_adaptation)                AS needs_adaptation,
    -- Worst severity wins, so "needs gutting" is not softened by a second ad
    -- that only mentions tired paintwork.
    MAX(CASE g.severity WHEN 'full' THEN 3 WHEN 'partial' THEN 2
                        WHEN 'cosmetic' THEN 1 END)                AS severity_rank,
    jsonb_object_agg(g.listing_id::text, g.evidence)
        FILTER (WHERE g.evidence <> '{}'::jsonb)                   AS evidence
FROM listings l
JOIN listing_gate g ON g.listing_id = l.id
GROUP BY 1;

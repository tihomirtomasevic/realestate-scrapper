-- On-demand crawl trigger.
--
-- The API and the crawler are separate containers, so "start a crawl now" needs
-- a channel between them. The database is already the shared dependency, so a
-- request table is the whole mechanism — no Docker socket handed to the API (a
-- container-escape hazard), no extra broker to run.

CREATE TABLE crawl_requests (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source       TEXT,                          -- NULL = every configured source
    requested_by TEXT NOT NULL DEFAULT 'ui',
    status       TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'done', 'failed', 'cancelled')),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    result       JSONB,
    error        TEXT
);

CREATE INDEX idx_crawl_requests_pending ON crawl_requests (requested_at)
    WHERE status = 'pending';
CREATE INDEX idx_crawl_requests_recent ON crawl_requests (requested_at DESC);

-- At most one crawl queued or in flight at a time: a second click while one is
-- running must not start a parallel crawl of the same site.
CREATE UNIQUE INDEX idx_crawl_requests_one_active ON crawl_requests ((status IN ('pending', 'running')))
    WHERE status IN ('pending', 'running');

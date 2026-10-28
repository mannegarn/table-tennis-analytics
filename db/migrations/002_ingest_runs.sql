-- 002: ingestion run history.
--
-- One row per fetch attempt, successful or failed. Append only: nothing updates
-- these rows, so they remain a truthful record of what happened. This is not a
-- scrape flag; eligibility is derived in the pending view (ADR 0005).

CREATE TABLE IF NOT EXISTS ingest.ingest_runs (
    run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source text NOT NULL,
    entity_key text NOT NULL,
    status text NOT NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL DEFAULT now(),
    http_status integer,
    row_count integer,
    payload_sha256 text,
    error text,
    CONSTRAINT ingest_runs_status_check
        CHECK (status IN ('succeeded', 'failed')),
    CONSTRAINT ingest_runs_window_check
        CHECK (finished_at >= started_at),
    CONSTRAINT ingest_runs_http_status_check
        CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
    CONSTRAINT ingest_runs_row_count_check
        CHECK (row_count IS NULL OR row_count >= 0),
    CONSTRAINT ingest_runs_sha256_check
        CHECK (payload_sha256 IS NULL OR payload_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS ingest_runs_entity_idx
    ON ingest.ingest_runs (source, entity_key, started_at DESC);

CREATE INDEX IF NOT EXISTS ingest_runs_failures_idx
    ON ingest.ingest_runs (finished_at DESC)
    WHERE status = 'failed';

COMMENT ON TABLE ingest.ingest_runs IS
    'Append-only record of every ingestion attempt, successful or not.';
COMMENT ON COLUMN ingest.ingest_runs.source IS
    'Source identifier, for example wtt_eventcalendar.';
COMMENT ON COLUMN ingest.ingest_runs.entity_key IS
    'Natural key of the entity attempted, for example the year 2023.';
COMMENT ON COLUMN ingest.ingest_runs.status IS
    'succeeded when the payload validated and landed, failed otherwise.';
COMMENT ON COLUMN ingest.ingest_runs.error IS
    'Human readable failure reason. Never populated for a success.';

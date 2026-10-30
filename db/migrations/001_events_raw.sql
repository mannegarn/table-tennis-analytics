-- 001: ingestion-owned raw landing table for the WTT event calendar.
--
-- One row per year. JSONB is used so SQL can unpack it later. The SHA-256 is
-- calculated over the original response bytes, not over the normalised JSONB.
--
-- The three count columns say who each number belongs to. Two are WTT's and
-- only one is ours, so the names state which:
--
--   source_original_count  WTT's Original_Count
--   source_final_count     WTT's Count
--   received_count         rows we actually parsed

CREATE SCHEMA IF NOT EXISTS ingest;
CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS ingest.events_by_year_raw (
    year integer PRIMARY KEY,
    payload jsonb NOT NULL,
    http_status integer NOT NULL,
    source_final_count integer,
    source_original_count integer,
    received_count integer NOT NULL,
    payload_sha256 text NOT NULL,
    source text NOT NULL DEFAULT 'wtt_eventcalendar',
    fetched_at timestamptz NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT events_by_year_raw_year_check
        CHECK (year BETWEEN 2000 AND 2100),
    CONSTRAINT events_by_year_raw_status_check
        CHECK (http_status BETWEEN 100 AND 599),
    CONSTRAINT events_by_year_raw_received_count_check
        CHECK (received_count >= 0),
    CONSTRAINT events_by_year_raw_sha256_check
        CHECK (payload_sha256 ~ '^[0-9a-f]{64}$')
);

COMMENT ON TABLE ingest.events_by_year_raw IS
    'One raw WTT event-calendar response per year, with fetch provenance.';
COMMENT ON COLUMN ingest.events_by_year_raw.payload IS
    'Response body as JSONB. Key order and whitespace are normalised.';
COMMENT ON COLUMN ingest.events_by_year_raw.source_original_count IS
    'WTT Original_Count: their total before their own internal step. '
    'Undocumented upstream, always >= source_final_count, informational only.';
COMMENT ON COLUMN ingest.events_by_year_raw.source_final_count IS
    'WTT Count: how many rows the API says it returned. '
    'Verified against received_count before a year is accepted.';
COMMENT ON COLUMN ingest.events_by_year_raw.received_count IS
    'Rows we parsed from payload -> 0 -> rows. Ours, not theirs. '
    'A mismatch against source_final_count fails the year.';
COMMENT ON COLUMN ingest.events_by_year_raw.payload_sha256 IS
    'SHA-256 of the original response bytes.';
COMMENT ON COLUMN ingest.events_by_year_raw.fetched_at IS
    'When the request completed.';
COMMENT ON COLUMN ingest.events_by_year_raw.ingested_at IS
    'When the row was written.';

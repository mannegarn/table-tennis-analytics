-- 003: unpacked event rows, owned by ingestion.
--
-- Structure only: JSON to columns, no trimming, no filtering and no business
-- rules (ADR 0003). One row per WTT event, keyed by EventId, so the scrape
-- queue is an indexed join instead of a JSONB scan.
--
-- Checked against the landed payloads before writing:
--   755 event rows across 2021-2026 collapse to 736 distinct EventIds, all
--   duplicates are exact repeats inside a single year, no EventId appears in
--   two different years, no name is blank and no end date precedes its start.
--   The unpack statement must therefore de-duplicate before inserting, because
--   one INSERT ... ON CONFLICT DO UPDATE cannot touch the same row twice.

CREATE TABLE IF NOT EXISTS ingest.events_unpacked (
    event_id bigint PRIMARY KEY,
    event_name text NOT NULL,
    start_date date NOT NULL,
    end_date date NOT NULL,
    source_year smallint NOT NULL,
    refreshed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT events_unpacked_name_check
        CHECK (btrim(event_name) <> ''),
    CONSTRAINT events_unpacked_dates_check
        CHECK (end_date >= start_date),
    CONSTRAINT events_unpacked_source_year_check
        CHECK (source_year BETWEEN 2000 AND 2100)
);

-- The live-window predicate reads end_date, so it gets its own index.
CREATE INDEX IF NOT EXISTS events_unpacked_end_date_idx
    ON ingest.events_unpacked (end_date);

COMMENT ON TABLE ingest.events_unpacked IS
    'One row per WTT event, unpacked from ingest.events_by_year_raw.';
COMMENT ON COLUMN ingest.events_unpacked.event_id IS
    'WTT EventId. Globally unique, so it is the key rather than the year.';
COMMENT ON COLUMN ingest.events_unpacked.start_date IS
    'StartDateTime as a date. The API sends no timezone, so none is invented.';
COMMENT ON COLUMN ingest.events_unpacked.end_date IS
    'EndDateTime as a date. Drives the still-live window in the pending view.';
COMMENT ON COLUMN ingest.events_unpacked.source_year IS
    'Calendar year the row was unpacked from. A fact, not the identity.';
COMMENT ON COLUMN ingest.events_unpacked.refreshed_at IS
    'Set on every upsert. The only thing the staleness rule reads.';

-- 003: unpacked event rows, owned by ingestion.
--
-- Structure only: JSON to columns, no trimming, no filtering and no business
-- rules (ADR 0003). One row per WTT event per calendar that listed it, so the
-- scrape queue is an indexed join instead of a JSONB scan.
--
-- Checked against the landed payloads before writing:
--   755 event rows across 2021-2026 collapse to 736 distinct EventIds, all
--   duplicates are exact repeats inside a single year, no EventId appears in
--   two different years, no name is blank and no end date precedes its start.
--   The unpack statement must therefore de-duplicate before inserting, because
--   one INSERT ... ON CONFLICT DO UPDATE cannot touch the same row twice.
--
-- WHY THE KEY IS (source_year, event_id)
--   A tournament that straddles New Year is listed in both years' payloads, so
--   event_id alone would let the later landing overwrite the earlier row and
--   quietly rewrite its source_year. Keying on the pair keeps both sightings.
--
-- FRESHNESS
--   first_seen_at is written once, on the first insert, and never touched
--   again. last_seen_at is rewritten on every unpack, so "still in the
--   calendar" means "last_seen_at moved". There is no DELETE or TRUNCATE: an
--   event WTT dropped simply stops advancing and keeps its row, so nothing
--   already pointing at it is orphaned.
--
-- NOT NULL
--   Every operational column is non-nullable by contract. The unpack statement
--   pulls values out of JSON with ``->>``, and a missing JSON key yields SQL
--   NULL instead of an error, so these constraints turn an upstream rename
--   ("EventName" -> "Event_Name") into a failed transaction rather than a
--   quietly corrupt row (ADR 0003: fail loudly on schema drift). end_date is
--   NOT NULL too: EndDateTime is part of the documented payload contract, so a
--   NULL there is drift, not a one-day tournament.

CREATE TABLE IF NOT EXISTS ingest.events_unpacked (
    source_year smallint NOT NULL,
    event_id bigint NOT NULL,
    event_name text NOT NULL,
    start_date date NOT NULL,
    end_date date NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT events_unpacked_pkey PRIMARY KEY (source_year, event_id),
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

-- The key leads with source_year, so a lookup by event_id alone has no index.
-- Cross-year questions ("did this event appear in two calendars") are exactly
-- what the key is for, so that path gets its own index.
CREATE INDEX IF NOT EXISTS events_unpacked_event_id_idx
    ON ingest.events_unpacked (event_id);

COMMENT ON TABLE ingest.events_unpacked IS
    'One row per WTT event per calendar, unpacked from ingest.events_by_year_raw.';
COMMENT ON COLUMN ingest.events_unpacked.event_id IS
    'WTT EventId. Unique per calendar, not across calendars: see the key.';
COMMENT ON COLUMN ingest.events_unpacked.event_name IS
    'EventName as sent. A NULL here means the unpack stopped finding the key.';
COMMENT ON COLUMN ingest.events_unpacked.start_date IS
    'StartDateTime as a date. The API sends no timezone, so none is invented. '
    'NOT NULL: a missing StartDateTime is schema drift, not a valid event.';
COMMENT ON COLUMN ingest.events_unpacked.end_date IS
    'EndDateTime as a date. Drives the still-live window in the pending view. '
    'NOT NULL because EndDateTime is part of the documented payload contract.';
COMMENT ON COLUMN ingest.events_unpacked.source_year IS
    'Calendar year whose payload listed this event. Part of the key, because '
    'a few tournaments appear in two years'' calendars.';
COMMENT ON COLUMN ingest.events_unpacked.first_seen_at IS
    'When this (source_year, event_id) was first unpacked. Written once.';
COMMENT ON COLUMN ingest.events_unpacked.last_seen_at IS
    'When this event was last present in its calendar payload. Advances on '
    'every unpack, so a stalled value means WTT dropped the event.';

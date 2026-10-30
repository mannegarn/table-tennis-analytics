-- 005: freshness-aware identity for unpacked events.
--
-- Two problems with the 003 shape, both fixed here without deleting anything:
--
--   1. The key was event_id alone, but about a dozen tournaments legitimately
--      appear in two calendar payloads (a December event listed again in the
--      following January's calendar). Whichever year was landed last overwrote
--      the other's row, so source_year was not trustworthy. The key becomes
--      (source_year, event_id): one row per event per calendar that listed it.
--
--   2. refreshed_at said when the most recent unpack ran, but nothing said when
--      an event first appeared, so there was no way to tell "still scheduled"
--      from "quietly dropped upstream". first_seen_at and last_seen_at replace
--      it: first_seen_at is written once, last_seen_at every time the event is
--      seen again in a payload.
--
-- Still no DELETE or TRUNCATE. An event missing from the latest payload keeps
-- its row and its last_seen_at stops advancing, which is the signal ingestion
-- wanted; hard deletes would orphan anything already keyed to the event.
--
-- refreshed_at is dropped rather than kept: last_seen_at is the same fact and
-- two columns recording "when did this last run" would drift apart. Its values
-- are copied into both new columns first, so the existing history survives.

ALTER TABLE ingest.events_unpacked
    ADD COLUMN IF NOT EXISTS first_seen_at timestamptz NOT NULL
        DEFAULT clock_timestamp(),
    ADD COLUMN IF NOT EXISTS last_seen_at timestamptz NOT NULL
        DEFAULT clock_timestamp();

-- Pre-existing rows only ever had refreshed_at; treat it as both the first and
-- the most recent sighting, which is the closest honest reading we have.
UPDATE ingest.events_unpacked
SET
    first_seen_at = refreshed_at,
    last_seen_at = refreshed_at
WHERE refreshed_at IS NOT NULL;

ALTER TABLE ingest.events_unpacked
    DROP COLUMN IF EXISTS refreshed_at;

-- Swap the key. The default constraint name from 003 is events_unpacked_pkey;
-- IF EXISTS keeps a re-run against an already-migrated database harmless.
ALTER TABLE ingest.events_unpacked
    DROP CONSTRAINT IF EXISTS events_unpacked_pkey;

ALTER TABLE ingest.events_unpacked
    ADD CONSTRAINT events_unpacked_pkey PRIMARY KEY (source_year, event_id);

-- The key now leads with source_year, so a lookup by event_id alone no longer
-- has an index. Cross-year questions ("did this event appear in two calendars")
-- are exactly what the new key is for, so that path gets its own index.
CREATE INDEX IF NOT EXISTS events_unpacked_event_id_idx
    ON ingest.events_unpacked (event_id);

COMMENT ON COLUMN ingest.events_unpacked.event_id IS
    'WTT EventId. Unique per calendar, not across calendars: see the key.';
COMMENT ON COLUMN ingest.events_unpacked.source_year IS
    'Calendar year whose payload listed this event. Part of the key, because '
    'a few tournaments appear in two years'' calendars.';
COMMENT ON COLUMN ingest.events_unpacked.first_seen_at IS
    'When this (source_year, event_id) was first unpacked. Written once.';
COMMENT ON COLUMN ingest.events_unpacked.last_seen_at IS
    'When this event was last present in its calendar payload. Advances on '
    'every unpack, so a stalled value means WTT dropped the event.';

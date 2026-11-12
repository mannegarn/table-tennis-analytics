-- 009: unpacked event sponsor rows, owned by ingestion.
--
-- Structure only: JSON to columns, no filtering and no business rules
-- (ADR 0003). One row per (event_id, event_sponsor_id) unpacked from
-- ingest.event_sponsors_raw.
--
-- NULLABILITY
--   Only event_id and event_sponsor_id are NOT NULL: they form the identity
--   and must be present by contract. All descriptive fields (sponsor_type_id,
--   sponsor_type_name, logo, dark_logo, sponsor_link) are nullable because
--   individual events may omit or send null for any of them.
--
-- INDEXING
--   The primary key (event_id, event_sponsor_id) indexes event_id as its
--   leading column for joins onto events. sponsor_type_id gets its own index
--   for filtering by sponsor category (e.g. ball or table sponsors).

CREATE TABLE IF NOT EXISTS ingest.event_sponsors_unpacked (
    event_id bigint NOT NULL,
    event_sponsor_id bigint NOT NULL,
    sponsor_type_id integer,
    sponsor_type_name text,
    logo text,
    dark_logo text,
    sponsor_link text,
    first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT event_sponsors_unpacked_pkey
        PRIMARY KEY (event_id, event_sponsor_id),
    CONSTRAINT event_sponsors_unpacked_event_id_check
        CHECK (event_id > 0),
    CONSTRAINT event_sponsors_unpacked_sponsor_id_check
        CHECK (event_sponsor_id > 0)
);

CREATE INDEX IF NOT EXISTS event_sponsors_unpacked_sponsor_type_id_idx
    ON ingest.event_sponsors_unpacked (sponsor_type_id);

COMMENT ON TABLE ingest.event_sponsors_unpacked IS
    'One row per sponsor per event, unpacked from ingest.event_sponsors_raw.';
COMMENT ON COLUMN ingest.event_sponsors_unpacked.event_id IS
    'WTT EventId this sponsor belongs to. Leading column of the primary key.';
COMMENT ON COLUMN ingest.event_sponsors_unpacked.event_sponsor_id IS
    'WTT eventSponsorId identifying the sponsor entry within the event.';
COMMENT ON COLUMN ingest.event_sponsors_unpacked.sponsor_type_id IS
    'WTT sponsorTypeId category identifier, nullable if omitted upstream.';
COMMENT ON COLUMN ingest.event_sponsors_unpacked.sponsor_type_name IS
    'WTT sponsorTypeName label (e.g. Official Ball), nullable if omitted.';
COMMENT ON COLUMN ingest.event_sponsors_unpacked.logo IS
    'Relative logo path from WTT, nullable if omitted.';
COMMENT ON COLUMN ingest.event_sponsors_unpacked.dark_logo IS
    'Relative dark-mode logo path from WTT, nullable if omitted.';
COMMENT ON COLUMN ingest.event_sponsors_unpacked.sponsor_link IS
    'Sponsor URL from WTT, nullable if omitted.';
COMMENT ON COLUMN ingest.event_sponsors_unpacked.first_seen_at IS
    'When this (event_id, event_sponsor_id) was first unpacked. Written once.';
COMMENT ON COLUMN ingest.event_sponsors_unpacked.last_seen_at IS
    'When this row was last present in an unpacked payload.';

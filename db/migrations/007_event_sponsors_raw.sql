-- 007: the event_sponsors raw landing table, owned by ingestion.
--
-- One row per EVENT (one API request to GetEventEquipmentwithLogo/{event_id}).
-- No unpacked table is created here yet because no downstream scraping depends
-- on sponsor fields; the raw JSONB payload is the source of truth and can be
-- unpacked later or read directly by dbt.
--
-- Empty responses ([]) are valid observations ("WTT has no sponsors listed for
-- this event") and are stored with received_count = 0 so the pending queue
-- knows the event has already been asked.

CREATE TABLE IF NOT EXISTS ingest.event_sponsors_raw (
    event_id bigint PRIMARY KEY,
    payload jsonb NOT NULL,
    http_status integer NOT NULL,
    received_count integer NOT NULL,
    payload_sha256 text NOT NULL,
    source text NOT NULL DEFAULT 'wtt_geteventequipmentwithlogo',
    fetched_at timestamptz NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT event_sponsors_raw_event_id_check
        CHECK (event_id > 0),
    CONSTRAINT event_sponsors_raw_status_check
        CHECK (http_status BETWEEN 100 AND 599),
    CONSTRAINT event_sponsors_raw_received_count_check
        CHECK (received_count >= 0),
    CONSTRAINT event_sponsors_raw_sha256_check
        CHECK (payload_sha256 ~ '^[0-9a-f]{64}$')
);

COMMENT ON TABLE ingest.event_sponsors_raw IS
    'One raw WTT GetEventEquipmentwithLogo response per event, with fetch provenance.';
COMMENT ON COLUMN ingest.event_sponsors_raw.event_id IS
    'WTT EventId these sponsors belong to. One row per event, so this is the key.';
COMMENT ON COLUMN ingest.event_sponsors_raw.payload IS
    'Response body as JSONB: a bare array of sponsor/equipment objects.';
COMMENT ON COLUMN ingest.event_sponsors_raw.received_count IS
    'Rows parsed from the payload, i.e. len(payload).';
COMMENT ON COLUMN ingest.event_sponsors_raw.payload_sha256 IS
    'SHA-256 of the original response bytes.';
COMMENT ON COLUMN ingest.event_sponsors_raw.fetched_at IS
    'When the request completed.';
COMMENT ON COLUMN ingest.event_sponsors_raw.ingested_at IS
    'When the row was written.';

-- 012: the match_commentary domain, owned by ingestion.
--
-- Two tables:
--   ingest.match_commentary_raw      one row per MATCH (event_id, document_code)
--   ingest.match_commentary_unpacked one row per (event_id, document_code, sequence_number)

CREATE TABLE IF NOT EXISTS ingest.match_commentary_raw (
    event_id bigint NOT NULL,
    document_code text NOT NULL,
    payload jsonb NOT NULL,
    http_status integer NOT NULL,
    received_count integer NOT NULL,
    payload_sha256 text NOT NULL,
    source text NOT NULL DEFAULT 'wtt_match_commentary',
    fetched_at timestamptz NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT match_commentary_raw_pkey
        PRIMARY KEY (event_id, document_code),
    CONSTRAINT match_commentary_raw_event_id_check
        CHECK (event_id > 0),
    CONSTRAINT match_commentary_raw_document_code_check
        CHECK (btrim(document_code) <> ''),
    CONSTRAINT match_commentary_raw_status_check
        CHECK (http_status BETWEEN 100 AND 599),
    CONSTRAINT match_commentary_raw_received_count_check
        CHECK (received_count >= 0),
    CONSTRAINT match_commentary_raw_sha256_check
        CHECK (payload_sha256 ~ '^[0-9a-f]{64}$')
);

COMMENT ON TABLE ingest.match_commentary_raw IS
    'One raw WTT match commentary response per match, decoded to a JSONB array.';

CREATE TABLE IF NOT EXISTS ingest.match_commentary_unpacked (
    event_id bigint NOT NULL,
    document_code text NOT NULL,
    sequence_number integer NOT NULL,
    commentary_id text,
    action_type text,
    commentary_text text,
    utc_date text,
    utc_time text,
    local_date text,
    local_time text,
    source_ts bigint,
    first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT match_commentary_unpacked_pkey
        PRIMARY KEY (event_id, document_code, sequence_number),
    CONSTRAINT match_commentary_unpacked_event_id_check
        CHECK (event_id > 0),
    CONSTRAINT match_commentary_unpacked_document_code_check
        CHECK (btrim(document_code) <> '')
);

CREATE INDEX IF NOT EXISTS match_commentary_unpacked_action_type_idx
    ON ingest.match_commentary_unpacked (action_type);

COMMENT ON TABLE ingest.match_commentary_unpacked IS
    'One row per commentary action inside a match, unpacked from ingest.match_commentary_raw.';

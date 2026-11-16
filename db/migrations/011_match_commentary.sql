-- 011: the match_commentary raw landing table, owned by ingestion.
--
-- One row per MATCH (event_id, document_code). No unpacked table is created in
-- ingest because no downstream scraper depends on commentary fields (ADR 0003);
-- full commentary unpacking lives in dbt staging (stg_match_commentary).

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

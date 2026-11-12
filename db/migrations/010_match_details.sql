-- 010: the match_details domain, owned by ingestion.
--
-- Two tables:
--   ingest.match_details_raw              one row per MATCH (event_id, document_code)
--   ingest.match_details_players_unpacked one row per (match, player_id)
--
-- Minimal unpack for now: only player_id is unpacked from
-- payload -> 'competitiors' -> 'players' -> 'playerId' so the players domain
-- can derive its pending queue without scanning 200k+ JSONB blobs.

CREATE TABLE IF NOT EXISTS ingest.match_details_raw (
    event_id bigint NOT NULL,
    document_code text NOT NULL,
    payload jsonb NOT NULL,
    http_status integer NOT NULL,
    payload_sha256 text NOT NULL,
    source text NOT NULL DEFAULT 'wtt_matchcentre',
    fetched_at timestamptz NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT match_details_raw_pkey
        PRIMARY KEY (event_id, document_code),
    CONSTRAINT match_details_raw_event_id_check
        CHECK (event_id > 0),
    CONSTRAINT match_details_raw_document_code_check
        CHECK (btrim(document_code) <> ''),
    CONSTRAINT match_details_raw_status_check
        CHECK (http_status BETWEEN 100 AND 599),
    CONSTRAINT match_details_raw_sha256_check
        CHECK (payload_sha256 ~ '^[0-9a-f]{64}$')
);

COMMENT ON TABLE ingest.match_details_raw IS
    'One raw WTT matchdata/matchcentre response per match, with fetch provenance.';
COMMENT ON COLUMN ingest.match_details_raw.payload IS
    'Response body as JSONB.';
COMMENT ON COLUMN ingest.match_details_raw.payload_sha256 IS
    'SHA-256 of the original response bytes.';

CREATE TABLE IF NOT EXISTS ingest.match_details_players_unpacked (
    event_id bigint NOT NULL,
    document_code text NOT NULL,
    player_id bigint NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT match_details_players_unpacked_pkey
        PRIMARY KEY (event_id, document_code, player_id),
    CONSTRAINT match_details_players_unpacked_event_id_check
        CHECK (event_id > 0),
    CONSTRAINT match_details_players_unpacked_document_code_check
        CHECK (btrim(document_code) <> ''),
    CONSTRAINT match_details_players_unpacked_player_id_check
        CHECK (player_id > 0)
);

CREATE INDEX IF NOT EXISTS match_details_players_unpacked_player_id_idx
    ON ingest.match_details_players_unpacked (player_id);

COMMENT ON TABLE ingest.match_details_players_unpacked IS
    'Minimal unpack of match_details_raw: distinct player_ids per match.';

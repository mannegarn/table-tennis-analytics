-- 013: the player_details raw landing table, owned by ingestion.
--
-- One row per PLAYER (player_id). No unpacked table is created in ingest_wtt because
-- no downstream scraper depends on player attributes (ADR 0003); full player
-- unpacking lives in dbt staging (stg_wtt_players).

CREATE TABLE IF NOT EXISTS ingest_wtt.player_details_raw (
    player_id bigint PRIMARY KEY,
    payload jsonb NOT NULL,
    http_status integer NOT NULL,
    received_count integer NOT NULL,
    payload_sha256 text NOT NULL,
    source text NOT NULL DEFAULT 'wtt_player_details',
    fetched_at timestamptz NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT player_details_raw_player_id_check
        CHECK (player_id > 0),
    CONSTRAINT player_details_raw_status_check
        CHECK (http_status BETWEEN 100 AND 599),
    CONSTRAINT player_details_raw_received_count_check
        CHECK (received_count >= 0),
    CONSTRAINT player_details_raw_sha256_check
        CHECK (payload_sha256 ~ '^[0-9a-f]{64}$')
);

COMMENT ON TABLE ingest_wtt.player_details_raw IS
    'One raw WTT TTU Connect GetPlayers response per player_id, with fetch provenance.';
COMMENT ON COLUMN ingest_wtt.player_details_raw.payload IS
    'Response body as JSONB.';
COMMENT ON COLUMN ingest_wtt.player_details_raw.payload_sha256 IS
    'SHA-256 of the original response bytes.';

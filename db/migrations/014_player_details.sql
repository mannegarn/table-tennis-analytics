-- 014: the player_details domain, owned by ingestion.
--
-- Two tables:
--   ingest.player_details_raw       one row per PLAYER (player_id)
--   ingest.player_details_unpacked  one flat row per PLAYER (player_id)

CREATE TABLE IF NOT EXISTS ingest.player_details_raw (
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

COMMENT ON TABLE ingest.player_details_raw IS
    'One raw WTT TTU Connect GetPlayers response per player_id, with fetch provenance.';
COMMENT ON COLUMN ingest.player_details_raw.payload IS
    'Response body as JSONB.';
COMMENT ON COLUMN ingest.player_details_raw.payload_sha256 IS
    'SHA-256 of the original response bytes.';

CREATE TABLE IF NOT EXISTS ingest.player_details_unpacked (
    player_id bigint PRIMARY KEY,
    player_name text,
    player_given_name text,
    player_family_name text,
    player_family_name_first text,
    country_code text,
    country_name text,
    nationality_code text,
    nationality_name text,
    organization_code text,
    organization_name text,
    gender text,
    age integer,
    dob text,
    handedness text,
    grip text,
    style text,
    equipment_sponsor text,
    blade_type text,
    racket_coloring_a text,
    racket_coloring_b text,
    racket_covering_a text,
    racket_covering_b text,
    active_since text,
    earnings_career text,
    earnings_ytd text,
    last_match text,
    next_match text,
    head_shot text,
    bio text,
    headshot_r text,
    headshot_l text,
    first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT player_details_unpacked_player_id_check
        CHECK (player_id > 0)
);

COMMENT ON TABLE ingest.player_details_unpacked IS
    'One flat profile row per player_id, unpacked from ingest.player_details_raw.';

-- 016: the ittf_player_profiles domain, owned by ingestion.
--
-- Single table storing extracted style/grip/hand enrichment per player_id,
-- populated either from a local CSV backfill (e.g. source = '20260615_master_players.csv')
-- or by live HTML scraping (source = 'ittf_results_link').

CREATE TABLE IF NOT EXISTS ingest.ittf_player_profiles (
    player_id bigint PRIMARY KEY,
    style text,
    grip text,
    hand text,
    source text NOT NULL,
    http_status integer,
    payload_sha256 text,
    fetched_at timestamptz NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ittf_player_profiles_player_id_check
        CHECK (player_id > 0),
    CONSTRAINT ittf_player_profiles_source_check
        CHECK (btrim(source) <> ''),
    CONSTRAINT ittf_player_profiles_status_check
        CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
    CONSTRAINT ittf_player_profiles_sha256_check
        CHECK (payload_sha256 IS NULL OR payload_sha256 ~ '^[0-9a-f]{64}$')
);

COMMENT ON TABLE ingest.ittf_player_profiles IS
    'Player style, grip, and hand enrichment per player_id from local CSV seed or ITTF results HTML.';
COMMENT ON COLUMN ingest.ittf_player_profiles.source IS
    'Origin of the row, e.g. 20260615_master_players.csv or ittf_results_link.';

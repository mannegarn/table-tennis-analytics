-- 019: analytics schema and Glicko-2 match-level rating tables.
--
-- Ratings are computed downstream of dbt (marts.fct_senior_singles_matches) by
-- `uv run tt-glicko-build` (`make glicko-run`) and recomputed from scratch on
-- each run so historical league backfills, identity merges, and parameter
-- changes stay completely consistent.

CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS analytics.glicko_run_metadata (
    run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    computed_at timestamptz NOT NULL DEFAULT now(),
    matches_rated integer NOT NULL,
    players_rated integer NOT NULL,
    config jsonb NOT NULL,
    CONSTRAINT glicko_run_metadata_matches_check
        CHECK (matches_rated >= 0),
    CONSTRAINT glicko_run_metadata_players_check
        CHECK (players_rated >= 0)
);

COMMENT ON TABLE analytics.glicko_run_metadata IS
    'Audit log of Glicko-2 rating runs and the exact configuration parameters used.';

CREATE TABLE IF NOT EXISTS analytics.fct_glicko_match_ratings (
    match_sk bigint PRIMARY KEY,
    event_sk bigint NOT NULL,
    gender_category text NOT NULL,
    rating_date date,
    p1_player_sk bigint NOT NULL,
    p2_player_sk bigint NOT NULL,
    p1_pre_rating double precision NOT NULL,
    p1_pre_rd double precision NOT NULL,
    p1_pre_vol double precision NOT NULL,
    p1_post_rating double precision NOT NULL,
    p1_post_rd double precision NOT NULL,
    p1_post_vol double precision NOT NULL,
    p2_pre_rating double precision NOT NULL,
    p2_pre_rd double precision NOT NULL,
    p2_pre_vol double precision NOT NULL,
    p2_post_rating double precision NOT NULL,
    p2_post_rd double precision NOT NULL,
    p2_post_vol double precision NOT NULL,
    p1_expected_win_prob double precision NOT NULL,
    p1_actual_score double precision NOT NULL,
    CONSTRAINT fct_glicko_match_ratings_prob_check
        CHECK (p1_expected_win_prob >= 0.0 AND p1_expected_win_prob <= 1.0),
    CONSTRAINT fct_glicko_match_ratings_score_check
        CHECK (p1_actual_score IN (0.0, 0.5, 1.0))
);

CREATE INDEX IF NOT EXISTS fct_glicko_match_ratings_gender_date_idx
    ON analytics.fct_glicko_match_ratings (gender_category, rating_date);

CREATE INDEX IF NOT EXISTS fct_glicko_match_ratings_p1_idx
    ON analytics.fct_glicko_match_ratings (p1_player_sk);

CREATE INDEX IF NOT EXISTS fct_glicko_match_ratings_p2_idx
    ON analytics.fct_glicko_match_ratings (p2_player_sk);

CREATE INDEX IF NOT EXISTS fct_glicko_match_ratings_event_idx
    ON analytics.fct_glicko_match_ratings (event_sk);

COMMENT ON TABLE analytics.fct_glicko_match_ratings IS
    'Pre- and post-match Glicko-2 ratings per rating-eligible senior singles match.';

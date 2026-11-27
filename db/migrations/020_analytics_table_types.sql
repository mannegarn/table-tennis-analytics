-- 020: Table-type brand dimension and isolated Glicko-2 match-level ratings.
--
-- Maps WTT event table sponsors into Asian, European, and Other groups and
-- stores separate per-table-group Glicko-2 rating trajectories (Asian and
-- European) computed downstream by `uv run tt-glicko-build` (`make glicko-run`).

CREATE TABLE IF NOT EXISTS analytics.dim_table_brands (
    table_sponsor text PRIMARY KEY,
    table_group text NOT NULL,
    notes text,
    CONSTRAINT dim_table_brands_group_check
        CHECK (table_group IN ('Asian', 'European', 'Other'))
);

COMMENT ON TABLE analytics.dim_table_brands IS
    'Mapping of WTT event table sponsors to table-style groups (Asian, European, Other).';

INSERT INTO analytics.dim_table_brands (table_sponsor, table_group, notes)
VALUES
    ('DHS', 'Asian', NULL),
    ('Double Fish', 'Asian', NULL),
    ('Yinhe', 'Asian', NULL),
    ('Andro', 'European', NULL),
    ('Butterfly', 'European', NULL),
    ('Donic', 'European', NULL),
    ('Joola', 'European', NULL),
    ('Stag', 'European', 'Assumed European-style table; manufactured in India'),
    ('Stiga', 'European', NULL),
    ('Tibhar', 'European', NULL),
    ('Nittaku', 'Other', NULL),
    ('San-Ei', 'Other', NULL),
    ('Xiom', 'Other', NULL)
ON CONFLICT (table_sponsor) DO UPDATE
SET
    table_group = excluded.table_group,
    notes = excluded.notes;

CREATE TABLE IF NOT EXISTS analytics.fct_table_type_glicko_match_ratings (
    match_sk bigint PRIMARY KEY,
    event_sk bigint NOT NULL,
    gender_category text NOT NULL,
    table_group text NOT NULL,
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
    CONSTRAINT fct_table_type_glicko_match_ratings_group_check
        CHECK (table_group IN ('Asian', 'European')),
    CONSTRAINT fct_table_type_glicko_match_ratings_prob_check
        CHECK (p1_expected_win_prob >= 0.0 AND p1_expected_win_prob <= 1.0),
    CONSTRAINT fct_table_type_glicko_match_ratings_score_check
        CHECK (p1_actual_score IN (0.0, 0.5, 1.0))
);

CREATE INDEX IF NOT EXISTS fct_table_type_glicko_match_ratings_gender_group_date_idx
    ON analytics.fct_table_type_glicko_match_ratings (
        gender_category, table_group, rating_date
    );

CREATE INDEX IF NOT EXISTS fct_table_type_glicko_match_ratings_p1_idx
    ON analytics.fct_table_type_glicko_match_ratings (p1_player_sk);

CREATE INDEX IF NOT EXISTS fct_table_type_glicko_match_ratings_p2_idx
    ON analytics.fct_table_type_glicko_match_ratings (p2_player_sk);

CREATE INDEX IF NOT EXISTS fct_table_type_glicko_match_ratings_event_idx
    ON analytics.fct_table_type_glicko_match_ratings (event_sk);

COMMENT ON TABLE analytics.fct_table_type_glicko_match_ratings IS
    'Pre- and post-match Glicko-2 ratings isolated by table-type pool (Asian vs European).';

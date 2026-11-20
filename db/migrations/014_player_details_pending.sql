-- 014: the player_details work queue, derived and never stored (ADR 0005).
--
-- Answers which player_ids seen in ingest_wtt.match_details_players_unpacked do
-- not yet have a row in ingest_wtt.player_details_raw.

CREATE OR REPLACE VIEW ingest_wtt.player_details_pending AS
WITH seen_players AS (
    SELECT
        p.player_id,
        MAX(p.last_seen_at) AS last_seen_at
    FROM ingest_wtt.match_details_players_unpacked AS p
    GROUP BY p.player_id
)
SELECT
    sp.player_id,
    'new'::text AS reason,
    r.fetched_at
FROM seen_players AS sp
LEFT JOIN ingest_wtt.player_details_raw AS r
    ON r.player_id = sp.player_id
WHERE r.player_id IS NULL;

COMMENT ON VIEW ingest_wtt.player_details_pending IS
    'Distinct players seen in match_details_players_unpacked with no row yet in player_details_raw.';

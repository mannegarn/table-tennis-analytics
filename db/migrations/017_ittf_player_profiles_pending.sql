-- 017: the ittf_player_profiles work queue, derived and never stored (ADR 0005).
--
-- Answers which player_ids seen in ingest.match_details_players_unpacked do
-- not yet have a row in ingest.ittf_player_profiles.

CREATE OR REPLACE VIEW ingest.ittf_player_profiles_pending AS
WITH seen_players AS (
    SELECT DISTINCT p.player_id
    FROM ingest.match_details_players_unpacked AS p
)
SELECT
    sp.player_id,
    'new'::text AS reason,
    r.fetched_at
FROM seen_players AS sp
LEFT JOIN ingest.ittf_player_profiles AS r
    ON r.player_id = sp.player_id
WHERE r.player_id IS NULL;

COMMENT ON VIEW ingest.ittf_player_profiles_pending IS
    'Distinct players seen in match_details_players_unpacked with no row yet in ittf_player_profiles.';

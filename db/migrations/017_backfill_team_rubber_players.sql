-- 017: backfill ingest_wtt.match_details_players_unpacked from nested team rubbers.
--
-- Team tie payloads in match_details_raw store individual rubbers inside
-- payload -> 'teamParentData' -> 'extended_info' -> 'matches' -> 'match_result'.
-- Earlier runs of unpack_match_details_players.sql only scanned top-level
-- competitiors, missing 20 players who only appeared in nested rubbers.

INSERT INTO ingest_wtt.match_details_players_unpacked (
    event_id,
    document_code,
    player_id,
    first_seen_at,
    last_seen_at
)
WITH team_rubber_players AS (
    SELECT
        r.event_id,
        r.document_code,
        coalesce(p.value ->> 'playerId', p.value ->> 'PlayerId') AS player_id_raw
    FROM ingest_wtt.match_details_raw AS r
    CROSS JOIN LATERAL jsonb_array_elements(
        r.payload -> 'teamParentData' -> 'extended_info' -> 'matches'
    ) AS rm
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN
                jsonb_typeof(rm.value -> 'match_result') = 'object'
                AND jsonb_typeof(
                    coalesce(
                        rm.value -> 'match_result' -> 'competitiors',
                        rm.value -> 'match_result' -> 'Competitiors'
                    )
                ) = 'array'
                THEN coalesce(
                    rm.value -> 'match_result' -> 'competitiors',
                    rm.value -> 'match_result' -> 'Competitiors'
                )
            ELSE '[]'::jsonb
        END
    ) AS c
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(coalesce(c.value -> 'players', c.value -> 'Players')) = 'array'
                THEN coalesce(c.value -> 'players', c.value -> 'Players')
            ELSE '[]'::jsonb
        END
    ) AS p
    WHERE
        r.document_code ~ '^TTE[MWX]TEAM'
        AND r.payload <> '{}'::jsonb
        AND jsonb_typeof(
            r.payload -> 'teamParentData' -> 'extended_info' -> 'matches'
        ) = 'array'
)

SELECT DISTINCT ON (
    t.event_id,
    t.document_code,
    t.player_id_raw::bigint
)
    t.event_id,
    t.document_code,
    t.player_id_raw::bigint AS player_id,
    clock_timestamp() AS first_seen_at,
    clock_timestamp() AS last_seen_at
FROM team_rubber_players AS t
WHERE
    t.player_id_raw ~ '^[0-9]+$'
    AND t.player_id_raw::bigint > 0
ORDER BY
    t.event_id,
    t.document_code,
    t.player_id_raw::bigint
ON CONFLICT (event_id, document_code, player_id) DO UPDATE SET
    last_seen_at = excluded.last_seen_at;

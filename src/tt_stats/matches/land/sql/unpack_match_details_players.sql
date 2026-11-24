-- =============================================================================
-- unpack_match_details_players.sql
--
-- Minimal unpack of match_details_raw: extracts distinct player_ids per match
-- from both top-level competitiors and nested team-tie rubbers
-- (teamParentData -> extended_info -> matches -> match_result -> competitiors)
-- so the players domain can derive its pending queue without scanning JSONB.
-- =============================================================================

INSERT INTO ingest_wtt.match_details_players_unpacked (
    event_id,
    document_code,
    player_id,
    first_seen_at,
    last_seen_at
)
WITH match_row AS (
    SELECT
        r.event_id,
        r.document_code,
        r.payload
    FROM ingest_wtt.match_details_raw AS r
    WHERE r.event_id = %(event_id)s
      AND r.document_code = %(document_code)s
      AND r.payload <> '{}'::jsonb
      AND jsonb_typeof(r.payload) = 'object'
),

top_level_players AS (
    SELECT DISTINCT
        m.event_id,
        m.document_code,
        coalesce(p.value ->> 'playerId', p.value ->> 'PlayerId') AS player_id_raw
    FROM match_row AS m
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(
                coalesce(m.payload -> 'competitiors', m.payload -> 'Competitiors')
            ) = 'array'
                THEN coalesce(m.payload -> 'competitiors', m.payload -> 'Competitiors')
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
),

team_rubber_players AS (
    SELECT DISTINCT
        m.event_id,
        m.document_code,
        coalesce(p.value ->> 'playerId', p.value ->> 'PlayerId') AS player_id_raw
    FROM match_row AS m
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(
                m.payload -> 'teamParentData' -> 'extended_info' -> 'matches'
            ) = 'array'
                THEN m.payload -> 'teamParentData' -> 'extended_info' -> 'matches'
            ELSE '[]'::jsonb
        END
    ) AS rm
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(rm.value -> 'match_result') = 'object'
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
),

all_players AS (
    SELECT
        event_id,
        document_code,
        player_id_raw
    FROM top_level_players
    UNION ALL
    SELECT
        event_id,
        document_code,
        player_id_raw
    FROM team_rubber_players
)

SELECT DISTINCT ON (a.event_id, a.document_code, a.player_id_raw::bigint)
       a.event_id                       AS event_id,
       a.document_code                  AS document_code,
       a.player_id_raw::bigint          AS player_id,
       clock_timestamp()                AS first_seen_at,
       clock_timestamp()                AS last_seen_at
FROM all_players AS a
WHERE a.player_id_raw ~ '^[0-9]+$'
  AND a.player_id_raw::bigint > 0
ORDER BY
    a.event_id,
    a.document_code,
    a.player_id_raw::bigint
ON CONFLICT (event_id, document_code, player_id) DO UPDATE SET
    last_seen_at = EXCLUDED.last_seen_at;

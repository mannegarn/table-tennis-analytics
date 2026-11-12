-- =============================================================================
-- unpack_match_details_players.sql
--
-- Minimal unpack of match_details_raw: extracts distinct player_ids per match
-- from payload -> 'competitiors' -> 'players' -> 'playerId' so the players
-- domain can derive its pending queue without scanning JSONB blobs.
-- =============================================================================

INSERT INTO ingest.match_details_players_unpacked (
    event_id,
    document_code,
    player_id,
    first_seen_at,
    last_seen_at
)
SELECT DISTINCT ON (r.event_id, r.document_code, (p.value ->> 'playerId')::bigint)
       r.event_id                               AS event_id,
       r.document_code                          AS document_code,
       (p.value ->> 'playerId')::bigint         AS player_id,
       clock_timestamp()                        AS first_seen_at,
       clock_timestamp()                        AS last_seen_at
FROM ingest.match_details_raw AS r
CROSS JOIN LATERAL jsonb_array_elements(r.payload -> 'competitiors') AS c
CROSS JOIN LATERAL jsonb_array_elements(c.value -> 'players') AS p
WHERE r.event_id = %(event_id)s
  AND r.document_code = %(document_code)s
  AND p.value ->> 'playerId' ~ '^[0-9]+$'
  AND (p.value ->> 'playerId')::bigint > 0
ORDER BY
    r.event_id,
    r.document_code,
    (p.value ->> 'playerId')::bigint
ON CONFLICT (event_id, document_code, player_id) DO UPDATE SET
    last_seen_at = EXCLUDED.last_seen_at;

-- =============================================================================
-- unpack_match_commentary.sql
--
-- Flattens one match's decoded commentary array from ingest.match_commentary_raw
-- into one row per (event_id, document_code, sequence_number) in
-- ingest.match_commentary_unpacked.
-- =============================================================================

INSERT INTO ingest.match_commentary_unpacked (
    event_id,
    document_code,
    sequence_number,
    commentary_id,
    action_type,
    commentary_text,
    utc_date,
    utc_time,
    local_date,
    local_time,
    source_ts,
    first_seen_at,
    last_seen_at
)
SELECT DISTINCT ON (
    r.event_id,
    r.document_code,
    (e.value -> 'data' ->> 'SEQUENCENUMBER')::integer
)
       r.event_id                                          AS event_id,
       r.document_code                                     AS document_code,
       (e.value -> 'data' ->> 'SEQUENCENUMBER')::integer   AS sequence_number,
       (e.value ->> 'id')::text                            AS commentary_id,
       (e.value -> 'data' ->> 'ACTIONTYPE')::text          AS action_type,
       (e.value -> 'data' ->> 'COMMENTARYTEXT')::text      AS commentary_text,
       (e.value -> 'data' ->> 'UTCDATE')::text             AS utc_date,
       (e.value -> 'data' ->> 'UTCTIME')::text             AS utc_time,
       (e.value -> 'data' ->> 'LOCALDATE')::text           AS local_date,
       (e.value -> 'data' ->> 'LOCALTIME')::text           AS local_time,
       (e.value ->> '_ts')::bigint                         AS source_ts,
       clock_timestamp()                                   AS first_seen_at,
       clock_timestamp()                                   AS last_seen_at
FROM ingest.match_commentary_raw AS r
CROSS JOIN LATERAL jsonb_array_elements(r.payload) AS e
WHERE r.event_id = %(event_id)s
  AND r.document_code = %(document_code)s
  AND e.value -> 'data' ->> 'SEQUENCENUMBER' ~ '^[0-9]+$'
ORDER BY
    r.event_id,
    r.document_code,
    (e.value -> 'data' ->> 'SEQUENCENUMBER')::integer
ON CONFLICT (event_id, document_code, sequence_number) DO UPDATE SET
    commentary_id   = EXCLUDED.commentary_id,
    action_type     = EXCLUDED.action_type,
    commentary_text = EXCLUDED.commentary_text,
    utc_date        = EXCLUDED.utc_date,
    utc_time        = EXCLUDED.utc_time,
    local_date      = EXCLUDED.local_date,
    local_time      = EXCLUDED.local_time,
    source_ts       = EXCLUDED.source_ts,
    last_seen_at    = EXCLUDED.last_seen_at;

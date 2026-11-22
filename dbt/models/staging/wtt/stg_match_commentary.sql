{{ config(
    materialized='table',
    pre_hook="set local enable_memoize = off",
) }}

-- match_commentary_raw is keyed by (event_id, document_code), so duplicate
-- sequence_number entries can only occur within a single match's payload array.
-- Deduplicating inside the LATERAL subquery sorts ~150 items per match in
-- memory instead of sorting all 6.7M+ rows globally. Disabling Memoize locally
-- prevents PostgreSQL from caching every unique non-empty JSONB payload
-- (triggered by the ~66% empty-array MCV frequency in pg_stats).
select
    r.event_id,
    r.document_code,
    c.sequence_number,
    c.commentary_id,
    c.action_type,
    c.commentary_text,
    c.utc_time
from {{ source('wtt', 'match_commentary_raw') }} as r
cross join
    lateral (
        select distinct on ((e.value -> 'data' ->> 'SEQUENCENUMBER')::integer)
            (e.value -> 'data' ->> 'SEQUENCENUMBER')::integer as sequence_number,
            nullif(btrim(e.value ->> 'id'), '') as commentary_id,
            nullif(btrim(e.value -> 'data' ->> 'ACTIONTYPE'), '') as action_type,
            nullif(btrim(e.value -> 'data' ->> 'COMMENTARYTEXT'), '') as commentary_text,
            nullif(btrim(e.value -> 'data' ->> 'UTCTIME'), '') as utc_time
        from jsonb_array_elements(r.payload) with ordinality as e (value, ord)
        where e.value -> 'data' ->> 'SEQUENCENUMBER' ~ '^[0-9]+$'
        order by
            (e.value -> 'data' ->> 'SEQUENCENUMBER')::integer,
            e.ord
    ) as c
where r.received_count > 0

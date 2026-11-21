{{ config(materialized='table') }}

select distinct on (
    r.event_id,
    r.document_code,
    (e.value -> 'data' ->> 'SEQUENCENUMBER')::integer
)
    r.event_id,
    r.document_code,
    (e.value -> 'data' ->> 'SEQUENCENUMBER')::integer as sequence_number,
    nullif(btrim(e.value ->> 'id'), '') as commentary_id,
    nullif(btrim(e.value -> 'data' ->> 'ACTIONTYPE'), '') as action_type,
    nullif(btrim(e.value -> 'data' ->> 'COMMENTARYTEXT'), '') as commentary_text,
    nullif(btrim(e.value -> 'data' ->> 'UTCTIME'), '') as utc_time
from {{ source('wtt', 'match_commentary_raw') }} as r
cross join lateral jsonb_array_elements(r.payload) as e
where e.value -> 'data' ->> 'SEQUENCENUMBER' ~ '^[0-9]+$'
order by
    r.event_id,
    r.document_code,
    (e.value -> 'data' ->> 'SEQUENCENUMBER')::integer

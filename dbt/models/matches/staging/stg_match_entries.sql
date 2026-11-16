{{ config(materialized='table') }}

select distinct on (
    r.event_id,
    nullif(btrim(elem ->> 'documentCode'), '')
)
    r.event_id,
    nullif(btrim(elem ->> 'documentCode'), '') as document_code,
    coalesce((elem ->> 'id')::bigint, (elem ->> 'iD')::bigint) as match_id,
    nullif(btrim(elem ->> 'subEventType'), '') as sub_event_type,
    nullif(btrim(elem ->> 'fullResults'), '') as full_results,
    case
        when nullif(btrim(elem ->> 'startDateLocal'), '') = '0001-01-01T00:00:00' then null
        else nullif(btrim(elem ->> 'startDateLocal'), '')::timestamp
    end as start_date_local
from {{ source('ingest', 'match_entries_payloads_raw') }} as r
cross join lateral jsonb_array_elements(r.payload) as elem
order by
    r.event_id,
    nullif(btrim(elem ->> 'documentCode'), '')

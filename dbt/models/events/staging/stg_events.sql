{{ config(materialized='table') }}

select distinct on (
    r.year,
    (e.value ->> 'EventId')::bigint
)
    r.year::smallint as source_year,
    (e.value ->> 'EventId')::bigint as event_id,
    nullif(btrim(e.value ->> 'EventName'), '') as event_name,
    nullif(btrim(e.value ->> 'EventCode'), '') as event_code,
    (e.value ->> 'EventTypeId')::integer as event_type_id,
    nullif(btrim(e.value ->> 'EventType'), '') as event_type,
    nullif(btrim(e.value ->> 'Event_Tier_Name'), '') as event_tier_name,
    (e.value ->> 'StartDateTime')::date as start_date,
    (e.value ->> 'EndDateTime')::date as end_date,
    nullif(btrim(e.value ->> 'Country'), '') as country,
    nullif(btrim(e.value ->> 'City'), '') as city,
    nullif(btrim(e.value ->> 'ContinentCode'), '') as continent_code,
    nullif(btrim(e.value ->> 'Subcontinent'), '') as subcontinent
from {{ source('ingest', 'events_by_year_raw') }} as r
cross join lateral jsonb_array_elements(r.payload -> 0 -> 'rows') as e
where jsonb_typeof(r.payload -> 0 -> 'rows') = 'array'
order by
    r.year,
    (e.value ->> 'EventId')::bigint,
    (e.value ->> 'StartDateTime')::text desc nulls last

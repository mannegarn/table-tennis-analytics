{{ config(materialized='table') }}

select distinct on (
    r.event_id,
    (e.value ->> 'eventSponsorId')::bigint
)
    r.event_id,
    (e.value ->> 'eventSponsorId')::bigint as event_sponsor_id,
    (e.value ->> 'sponsorTypeId')::integer as sponsor_type_id,
    nullif(btrim(e.value ->> 'sponsorTypeName'), '') as sponsor_type_name,
    nullif(btrim(e.value ->> 'logo'), '') as logo,
    nullif(btrim(e.value ->> 'darkLogo'), '') as dark_logo,
    nullif(btrim(e.value ->> 'sponsorLink'), '') as sponsor_link
from {{ source('ingest', 'event_sponsors_raw') }} as r
cross join lateral jsonb_array_elements(r.payload) as e
order by
    r.event_id,
    (e.value ->> 'eventSponsorId')::bigint

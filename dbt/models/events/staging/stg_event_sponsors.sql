{{ config(materialized='view') }}

select
    event_id,
    event_sponsor_id,
    sponsor_type_id,
    nullif(btrim(sponsor_type_name), '') as sponsor_type_name,
    nullif(btrim(logo), '') as logo,
    nullif(btrim(dark_logo), '') as dark_logo,
    nullif(btrim(sponsor_link), '') as sponsor_link
from {{ source('ingest', 'event_sponsors_unpacked') }}

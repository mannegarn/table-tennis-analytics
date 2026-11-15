{{ config(materialized='view') }}

select
    event_id,
    nullif(btrim(document_code), '') as document_code,
    sequence_number,
    nullif(btrim(commentary_id), '') as commentary_id,
    nullif(btrim(action_type), '') as action_type,
    nullif(btrim(commentary_text), '') as commentary_text,
    nullif(btrim(utc_time), '') as utc_time
from {{ source('ingest', 'match_commentary_unpacked') }}

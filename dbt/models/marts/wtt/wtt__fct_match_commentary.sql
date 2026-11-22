{{ config(
    alias='fct_match_commentary',
    materialized='table',
    post_hook=[
        "create unique index on {{ this }} (event_id, document_code, sequence_number) include (action_type)",
        "analyze {{ this }}",
    ],
) }}

select
    c.event_id,
    c.document_code,
    c.sequence_number,
    c.commentary_id,
    c.action_type,
    a.action_name,
    a.action_category,
    c.commentary_text,
    (
        c.commentary_text is not null
        and c.commentary_text <> 'No commentary available for this action'
    ) as has_commentary_text,
    c.utc_time
from {{ ref('stg_match_commentary') }} as c
left join {{ ref('action_types') }} as a
    on c.action_type = a.action_type

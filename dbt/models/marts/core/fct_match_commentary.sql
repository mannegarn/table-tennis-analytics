select
    match_sk,
    event_id,
    document_code,
    sequence_number,
    commentary_id,
    action_type,
    action_name,
    action_category,
    commentary_text,
    has_commentary_text,
    utc_time
from {{ ref('wtt__fct_match_commentary') }}

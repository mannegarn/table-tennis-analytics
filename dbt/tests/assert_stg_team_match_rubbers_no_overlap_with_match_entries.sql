select
    r.event_id,
    r.document_code
from {{ ref('stg_team_match_rubbers') }} as r
inner join {{ ref('stg_match_entries') }} as e
    on
        r.event_id = e.event_id
        and r.document_code = e.document_code

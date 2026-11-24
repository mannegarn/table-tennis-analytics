select
    event_id,
    document_code,
    rubber_number
from {{ ref('stg_team_match_rubbers') }}
where rubber_number not between 1 and 5

select
    event_id,
    document_code,
    count(*) as row_count
from {{ ref('match_score_overrides') }}
group by 1, 2
having count(*) > 1

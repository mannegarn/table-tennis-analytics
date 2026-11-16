select
    event_id,
    document_code,
    count(*) as row_count
from {{ ref('stg_match_entries') }}
group by 1, 2
having count(*) > 1

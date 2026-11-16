select
    event_id,
    document_code,
    sequence_number,
    count(*) as row_count
from {{ ref('stg_match_commentary') }}
group by 1, 2, 3
having count(*) > 1

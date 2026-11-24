select
    event_id,
    document_code,
    count(*) as row_count
from {{ ref('int_wtt_senior_singles') }}
group by 1, 2
having count(*) > 1

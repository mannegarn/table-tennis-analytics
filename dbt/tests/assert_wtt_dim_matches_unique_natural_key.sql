select
    source_system,
    source_event_id,
    document_code,
    count(*) as row_count
from {{ ref('wtt__dim_matches') }}
group by 1, 2, 3
having count(*) > 1

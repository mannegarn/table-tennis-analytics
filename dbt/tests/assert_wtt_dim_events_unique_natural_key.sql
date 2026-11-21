select
    source_system,
    source_event_id,
    count(*) as row_count
from {{ ref('wtt__dim_events') }}
group by 1, 2
having count(*) > 1

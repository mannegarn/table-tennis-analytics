select
    source_year,
    event_id,
    count(*) as row_count
from {{ ref('stg_events') }}
group by 1, 2
having count(*) > 1

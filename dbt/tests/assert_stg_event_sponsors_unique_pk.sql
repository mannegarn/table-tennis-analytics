select
    event_id,
    event_sponsor_id,
    count(*) as row_count
from {{ ref('stg_event_sponsors') }}
group by 1, 2
having count(*) > 1

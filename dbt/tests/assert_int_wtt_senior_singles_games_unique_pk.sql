select
    event_id,
    document_code,
    game_number,
    count(*) as row_count
from {{ ref('int_wtt_senior_singles_games') }}
group by 1, 2, 3
having count(*) > 1

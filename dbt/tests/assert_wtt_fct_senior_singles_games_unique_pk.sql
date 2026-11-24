select
    match_sk,
    game_number,
    count(*) as row_count
from {{ ref('wtt__fct_senior_singles_games') }}
group by 1, 2
having count(*) > 1

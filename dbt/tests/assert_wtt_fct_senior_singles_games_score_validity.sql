{{ config(severity='warn') }}

-- Surface any individual game rows failing table-tennis scoring rules
-- (0 expected today).
select
    match_sk,
    game_number,
    p1_points,
    p2_points,
    point_margin
from {{ ref('wtt__fct_senior_singles_games') }}
where not is_valid_game_score

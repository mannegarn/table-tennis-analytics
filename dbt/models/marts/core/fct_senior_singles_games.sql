select
    match_sk,
    game_number,
    p1_points,
    p2_points,
    point_margin,
    winner_side,
    is_valid_game_score
from {{ ref('wtt__fct_senior_singles_games') }}

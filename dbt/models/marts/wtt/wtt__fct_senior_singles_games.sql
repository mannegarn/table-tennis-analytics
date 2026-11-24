{{ config(
    alias='fct_senior_singles_games',
    materialized='table',
    post_hook=[
        "create unique index on {{ this }} (match_sk, game_number)",
        "analyze {{ this }}",
    ],
) }}

select
    fm.match_sk,
    g.game_number,
    g.p1_points,
    g.p2_points,
    g.point_margin,
    g.winner_side,
    g.is_valid_game_score
from {{ ref('int_wtt_senior_singles_games') }} as g
inner join {{ ref('wtt__fct_senior_singles_matches') }} as fm
    on
        g.event_id = fm.source_event_id
        and g.document_code = fm.document_code
where fm.result_type in ('played', 'draw')

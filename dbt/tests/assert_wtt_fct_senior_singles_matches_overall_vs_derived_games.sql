{{ config(severity='warn') }}

-- Surface any played/drawn senior singles matches where overall_scores disagrees
-- with derived game winners from fct_senior_singles_games (0 expected today).
with game_winners as (
    select
        match_sk,
        count(*) filter (where winner_side = 1)::smallint as derived_games_p1,
        count(*) filter (where winner_side = 2)::smallint as derived_games_p2
    from {{ ref('wtt__fct_senior_singles_games') }}
    group by match_sk
)

select
    fm.match_sk,
    fm.source_event_id,
    fm.document_code,
    fm.overall_score_raw,
    fm.games_won_p1,
    fm.games_won_p2,
    gw.derived_games_p1,
    gw.derived_games_p2
from {{ ref('wtt__fct_senior_singles_matches') }} as fm
left join game_winners as gw
    on fm.match_sk = gw.match_sk
where
    fm.result_type in ('played', 'draw')
    and (
        coalesce(gw.derived_games_p1, 0) <> fm.games_won_p1
        or coalesce(gw.derived_games_p2, 0) <> fm.games_won_p2
    )

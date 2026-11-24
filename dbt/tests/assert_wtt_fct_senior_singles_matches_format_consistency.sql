{{ config(severity='warn') }}

-- Surface any played/drawn senior singles matches whose game totals violate
-- their resolved match_format (0 expected today).
select
    match_sk,
    source_event_id,
    document_code,
    match_format,
    best_of,
    scheduled_games,
    overall_score_raw,
    games_won_p1,
    games_won_p2
from {{ ref('wtt__fct_senior_singles_matches') }}
where
    result_type in ('played', 'draw')
    and (
        (
            match_format like 'best_of_%'
            and (
                best_of is null
                or games_won_p1 = games_won_p2
                or greatest(games_won_p1, games_won_p2) <> ceil(best_of / 2.0)::integer
            )
        )
        or (
            match_format = 'fixed_4_games'
            and (games_won_p1 + games_won_p2) <> 4
        )
        or (
            match_format = 'fixed_3_games'
            and (games_won_p1 + games_won_p2) not between 1 and 3
        )
    )

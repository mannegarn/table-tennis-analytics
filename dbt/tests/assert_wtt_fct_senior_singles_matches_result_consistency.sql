-- Assert strict winner/loser/defaulting/score invariants across
-- fct_senior_singles_matches.
select
    match_sk,
    source_event_id,
    document_code,
    result_type,
    winner_side,
    defaulting_side
from {{ ref('wtt__fct_senior_singles_matches') }}
where
    (
        winner_side = 1
        and (
            winner_player_sk <> p1_player_sk
            or loser_player_sk <> p2_player_sk
            or winner_player_sk is null
            or loser_player_sk is null
        )
    )
    or (
        winner_side = 2
        and (
            winner_player_sk <> p2_player_sk
            or loser_player_sk <> p1_player_sk
            or winner_player_sk is null
            or loser_player_sk is null
        )
    )
    or (
        winner_side is null
        and (
            winner_player_sk is not null
            or loser_player_sk is not null
            or defaulting_side is not null
            or defaulting_player_sk is not null
        )
    )
    or (
        is_default_result
        and (
            games_won_p1 is not null
            or games_won_p2 is not null
            or points_won_p1 is not null
            or points_won_p2 is not null
            or is_rating_eligible
            or (
                winner_side is not null
                and (
                    defaulting_side <> (3 - winner_side)
                    or defaulting_player_sk <> loser_player_sk
                )
            )
        )
    )
    or (
        not is_default_result
        and (
            defaulting_side is not null
            or defaulting_player_sk is not null
        )
    )
    or (
        result_type = 'draw'
        and (
            winner_side is not null
            or games_won_p1 is null
            or games_won_p2 is null
            or games_won_p1 <> games_won_p2
        )
    )

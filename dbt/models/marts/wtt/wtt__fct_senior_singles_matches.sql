{{ config(
    alias='fct_senior_singles_matches',
    materialized='table',
    post_hook=[
        "create unique index on {{ this }} (match_sk)",
        "create index on {{ this }} (event_sk)",
        "create index on {{ this }} (p1_player_sk)",
        "create index on {{ this }} (p2_player_sk)",
        "create index on {{ this }} (winner_player_sk)",
        "analyze {{ this }}",
    ],
) }}

select
    dm.match_sk,
    dm.event_sk,
    dm.source_system,
    s.event_id as source_event_id,
    s.document_code,
    s.is_team_rubber,
    s.parent_document_code,
    s.rubber_number,
    p1.player_sk as p1_player_sk,
    s.p1_player_id,
    p1.player_name as p1_player_name,
    coalesce(s.p1_player_org, p1.organization_code) as p1_country_code,
    p2.player_sk as p2_player_sk,
    s.p2_player_id,
    p2.player_name as p2_player_name,
    coalesce(s.p2_player_org, p2.organization_code) as p2_country_code,
    s.winner_side,
    case
        when s.winner_side = 1 then p1.player_sk
        when s.winner_side = 2 then p2.player_sk
    end as winner_player_sk,
    case
        when s.winner_side = 1 then s.p1_player_id
        when s.winner_side = 2 then s.p2_player_id
    end as winner_player_id,
    case
        when s.winner_side = 1 then p1.player_name
        when s.winner_side = 2 then p2.player_name
    end as winner_player_name,
    case
        when s.winner_side = 1 then p2.player_sk
        when s.winner_side = 2 then p1.player_sk
    end as loser_player_sk,
    case
        when s.winner_side = 1 then s.p2_player_id
        when s.winner_side = 2 then s.p1_player_id
    end as loser_player_id,
    case
        when s.winner_side = 1 then p2.player_name
        when s.winner_side = 2 then p1.player_name
    end as loser_player_name,
    s.defaulting_side,
    case
        when s.defaulting_side = 1 then p1.player_sk
        when s.defaulting_side = 2 then p2.player_sk
    end as defaulting_player_sk,
    case
        when s.defaulting_side = 1 then s.p1_player_id
        when s.defaulting_side = 2 then s.p2_player_id
    end as defaulting_player_id,
    case
        when s.defaulting_side = 1 then p1.player_name
        when s.defaulting_side = 2 then p2.player_name
    end as defaulting_player_name,
    s.gender_category,
    s.round_code,
    s.round_stage,
    s.round_order,
    s.start_date_utc,
    s.start_date_local,
    s.match_format,
    s.best_of,
    s.scheduled_games,
    s.scoring_rule,
    s.max_points_per_game,
    s.advantage_per_game,
    s.sudden_death_point,
    s.result_type,
    s.is_default_result,
    s.is_rating_eligible,
    s.has_result,
    s.has_score_anomaly,
    s.has_invalid_game_score,
    s.overall_score_raw,
    s.games_won_p1,
    s.games_won_p2,
    s.points_won_p1,
    s.points_won_p2,
    s.p1_timeout,
    s.p2_timeout,
    s.p1_card_y,
    s.p2_card_y,
    s.p1_card_yr1,
    s.p2_card_yr1,
    s.p1_card_yr2,
    s.p2_card_yr2
from {{ ref('int_wtt_senior_singles') }} as s
inner join {{ ref('wtt__dim_matches') }} as dm
    on
        dm.source_system = 'wtt'
        and s.event_id = dm.source_event_id
        and s.document_code = dm.document_code
inner join {{ ref('wtt__dim_players') }} as p1
    on s.p1_player_id::text = p1.ittf_id
inner join {{ ref('wtt__dim_players') }} as p2
    on s.p2_player_id::text = p2.ittf_id
where not dm.is_para

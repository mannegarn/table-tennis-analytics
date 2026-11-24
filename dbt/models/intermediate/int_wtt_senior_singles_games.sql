{{ config(
    materialized='table',
    post_hook="analyze {{ this }}",
) }}

with senior_singles_raw as (
    select
        d.event_id,
        d.document_code,
        false as is_team_rubber,
        d.overall_scores,
        d.overall_games_p1,
        d.overall_games_p2,
        d.result_suffix,
        d.results_game_scores,
        coalesce(nullif(d.max_points_per_game, 0), 11) as target_points,
        coalesce(nullif(d.advantage_per_game, 0), 2) as advantage_points,
        d.sudden_death_point
    from {{ ref('stg_match_details') }} as d
    inner join {{ ref('stg_match_entries') }} as e
        on
            d.event_id = e.event_id
            and d.document_code = e.document_code
    where
        e.discipline = 'singles'
        and not e.is_youth
        and not e.is_para
        and d.result_status = 'OFFICIAL'
        and d.comp_1_player_2_id is null
        and d.comp_2_player_2_id is null

    union all

    select
        r.event_id,
        r.document_code,
        true as is_team_rubber,
        r.overall_scores,
        r.overall_games_p1,
        r.overall_games_p2,
        r.result_suffix,
        r.results_game_scores,
        coalesce(nullif(r.max_points_per_game, 0), 11) as target_points,
        coalesce(nullif(r.advantage_per_game, 0), 2) as advantage_points,
        r.sudden_death_point
    from {{ ref('stg_team_match_rubbers') }} as r
    where
        r.discipline = 'singles'
        and not r.is_youth
        and not r.is_para
        and r.result_status = 'OFFICIAL'
        -- Exclude unlabelled BYE forfeits and upstream-corrupted Event 2615
        -- R4/R5 rubbers where WTT copy-pasted wrong player IDs.
        and coalesce(r.comp_1_name, '') not ilike 'bye'
        and coalesce(r.comp_2_name, '') not ilike 'bye'
        and coalesce(r.comp_1_player_1_name, '') not ilike 'bye'
        and coalesce(r.comp_2_player_1_name, '') not ilike 'bye'
        and not (r.event_id = 2615 and r.rubber_number in (4, 5))
),

with_overrides as (
    select
        m.event_id,
        m.document_code,
        m.is_team_rubber,
        coalesce(so.results_game_scores, m.results_game_scores) as results_game_scores,
        case
            when so.overall_scores is not null
                then
                    regexp_match(
                        btrim(so.overall_scores),
                        '^([0-9]+)-([0-9]+)(?: ([A-Z]+))?$'
                    )
        end as override_score_parts,
        m.overall_games_p1,
        m.overall_games_p2,
        m.result_suffix,
        m.target_points,
        m.advantage_points,
        m.sudden_death_point
    from senior_singles_raw as m
    left join {{ ref('match_score_overrides') }} as so
        on
            m.event_id = so.event_id
            and m.document_code = so.document_code
),

eligible_matches as materialized (
    select
        event_id,
        document_code,
        is_team_rubber,
        results_game_scores,
        target_points,
        advantage_points,
        sudden_death_point
    from with_overrides
    where
        results_game_scores is not null
        and coalesce(override_score_parts[3], result_suffix) is null
        and coalesce(override_score_parts[1]::smallint, overall_games_p1) is not null
        and coalesce(override_score_parts[2]::smallint, overall_games_p2) is not null
        and (
            coalesce(override_score_parts[1]::smallint, overall_games_p1)
            + coalesce(override_score_parts[2]::smallint, overall_games_p2)
        ) > 0
),

unpacked as (
    select
        m.event_id,
        m.document_code,
        m.is_team_rubber,
        g.game_number::smallint as game_number,
        btrim(g.score_token) as score_token,
        m.target_points,
        m.advantage_points,
        m.sudden_death_point
    from eligible_matches as m
    cross join
        lateral unnest(string_to_array(m.results_game_scores, ','))
        with ordinality as g (score_token, game_number)
),

parsed as (
    select
        event_id,
        document_code,
        is_team_rubber,
        game_number,
        split_part(score_token, '-', 1)::smallint as p1_points,
        split_part(score_token, '-', 2)::smallint as p2_points,
        target_points,
        advantage_points,
        sudden_death_point
    from unpacked
    where
        score_token ~ '^[0-9]+-[0-9]+$'
        and score_token <> '0-0'
)

select
    event_id,
    document_code,
    is_team_rubber,
    game_number,
    p1_points,
    p2_points,
    abs(p1_points - p2_points)::smallint as point_margin,
    case
        when p1_points > p2_points then 1::smallint
        when p2_points > p1_points then 2::smallint
    end as winner_side,
    (
        (
            greatest(p1_points, p2_points) = target_points
            and abs(p1_points - p2_points) >= advantage_points
        )
        or (
            greatest(p1_points, p2_points) > target_points
            and abs(p1_points - p2_points) = advantage_points
        )
        or (
            -- WTT suddenDeathPoint is the tied score where sudden death begins
            -- (e.g. 6 -> 7-6 wins); 99 is the sentinel for no sudden death.
            sudden_death_point is not null
            and sudden_death_point < 99
            and greatest(p1_points, p2_points) = sudden_death_point + 1
            and least(p1_points, p2_points) = sudden_death_point
        )
    ) as is_valid_game_score
from parsed

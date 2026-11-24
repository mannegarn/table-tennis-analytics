{{ config(
    materialized='table',
    post_hook="analyze {{ this }}",
) }}

with senior_singles_union as (
    select
        d.event_id,
        d.document_code,
        false as is_team_rubber,
        null::text as parent_document_code,
        null::smallint as rubber_number,
        e.gender_category,
        e.raw_round_token,
        d.start_date_utc,
        d.start_date_local,
        d.best_of as raw_best_of,
        d.max_points_per_game,
        d.advantage_per_game,
        d.sudden_death_point,
        d.overall_scores as overall_score_raw,
        d.overall_games_p1,
        d.overall_games_p2,
        d.result_suffix,
        coalesce(
            d.comp_1_player_1_id,
            case
                when d.comp_1_id ~ '^[0-9]+$' and d.comp_1_id::bigint > 0
                    then d.comp_1_id::bigint
            end
        ) as p1_player_id,
        coalesce(d.comp_1_player_1_org, d.comp_1_org) as p1_player_org,
        coalesce(
            d.comp_2_player_1_id,
            case
                when d.comp_2_id ~ '^[0-9]+$' and d.comp_2_id::bigint > 0
                    then d.comp_2_id::bigint
            end
        ) as p2_player_id,
        coalesce(d.comp_2_player_1_org, d.comp_2_org) as p2_player_org,
        d.comp_1_timeout as p1_timeout,
        d.comp_2_timeout as p2_timeout,
        d.comp_1_card_y as p1_card_y,
        d.comp_2_card_y as p2_card_y,
        d.comp_1_card_yr1 as p1_card_yr1,
        d.comp_2_card_yr1 as p2_card_yr1,
        d.comp_1_card_yr2 as p1_card_yr2,
        d.comp_2_card_yr2 as p2_card_yr2
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
        r.parent_document_code,
        r.rubber_number,
        r.gender_category,
        r.raw_round_token,
        r.start_date_utc,
        r.start_date_local,
        r.best_of as raw_best_of,
        r.max_points_per_game,
        r.advantage_per_game,
        r.sudden_death_point,
        r.overall_scores as overall_score_raw,
        r.overall_games_p1,
        r.overall_games_p2,
        r.result_suffix,
        r.comp_1_player_1_id as p1_player_id,
        coalesce(r.comp_1_player_1_org, r.comp_1_org) as p1_player_org,
        r.comp_2_player_1_id as p2_player_id,
        coalesce(r.comp_2_player_1_org, r.comp_2_org) as p2_player_org,
        r.comp_1_timeout as p1_timeout,
        r.comp_2_timeout as p2_timeout,
        r.comp_1_card_y as p1_card_y,
        r.comp_2_card_y as p2_card_y,
        r.comp_1_card_yr1 as p1_card_yr1,
        r.comp_2_card_yr1 as p2_card_yr1,
        r.comp_1_card_yr2 as p1_card_yr2,
        r.comp_2_card_yr2 as p2_card_yr2
    from {{ ref('stg_team_match_rubbers') }} as r
    where
        r.discipline = 'singles'
        and not r.is_youth
        and not r.is_para
        and r.result_status = 'OFFICIAL'
        -- Exclude unplayed dead rubbers (NULL or 0-0 with no default suffix).
        and (
            r.result_suffix is not null
            or (
                r.overall_games_p1 is not null
                and r.overall_games_p2 is not null
                and (r.overall_games_p1 + r.overall_games_p2) > 0
            )
        )
        -- Exclude unlabelled BYE forfeits and upstream-corrupted Event 2615
        -- R4/R5 rubbers where WTT copy-pasted wrong player IDs.
        and coalesce(r.comp_1_name, '') not ilike 'bye'
        and coalesce(r.comp_2_name, '') not ilike 'bye'
        and coalesce(r.comp_1_player_1_name, '') not ilike 'bye'
        and coalesce(r.comp_2_player_1_name, '') not ilike 'bye'
        and not (r.event_id = 2615 and r.rubber_number in (4, 5))
),

game_totals as (
    select
        event_id,
        document_code,
        count(*) filter (where winner_side = 1)::smallint as derived_games_p1,
        count(*) filter (where winner_side = 2)::smallint as derived_games_p2,
        sum(p1_points)::integer as points_won_p1,
        sum(p2_points)::integer as points_won_p2,
        count(*) filter (where not is_valid_game_score)::integer as invalid_game_count
    from {{ ref('int_wtt_senior_singles_games') }}
    group by event_id, document_code
),

with_score_overrides as (
    select
        m.event_id,
        m.document_code,
        m.is_team_rubber,
        m.parent_document_code,
        m.rubber_number,
        m.gender_category,
        m.raw_round_token,
        m.start_date_utc,
        m.start_date_local,
        m.raw_best_of,
        m.max_points_per_game,
        m.advantage_per_game,
        m.sudden_death_point,
        coalesce(so.overall_scores, m.overall_score_raw) as overall_score_raw,
        case
            when so.overall_scores is not null
                then
                    (
                        regexp_match(
                            btrim(so.overall_scores),
                            '^([0-9]+)-([0-9]+)(?: ([A-Z]+))?$'
                        )
                    )[1]::smallint
            else m.overall_games_p1
        end as overall_games_p1,
        case
            when so.overall_scores is not null
                then
                    (
                        regexp_match(
                            btrim(so.overall_scores),
                            '^([0-9]+)-([0-9]+)(?: ([A-Z]+))?$'
                        )
                    )[2]::smallint
            else m.overall_games_p2
        end as overall_games_p2,
        case
            when so.overall_scores is not null
                then
                    (
                        regexp_match(
                            btrim(so.overall_scores),
                            '^([0-9]+)-([0-9]+)(?: ([A-Z]+))?$'
                        )
                    )[3]
            else m.result_suffix
        end as result_suffix,
        m.p1_player_id,
        m.p1_player_org,
        m.p2_player_id,
        m.p2_player_org,
        m.p1_timeout,
        m.p2_timeout,
        m.p1_card_y,
        m.p2_card_y,
        m.p1_card_yr1,
        m.p2_card_yr1,
        m.p1_card_yr2,
        m.p2_card_yr2
    from senior_singles_union as m
    left join {{ ref('match_score_overrides') }} as so
        on
            m.event_id = so.event_id
            and m.document_code = so.document_code
),

enriched as (
    select
        m.event_id,
        m.document_code,
        m.is_team_rubber,
        m.parent_document_code,
        m.rubber_number,
        m.gender_category,
        m.raw_round_token,
        rc.round_code,
        rc.round_stage,
        rc.round_order,
        m.start_date_utc,
        m.start_date_local,
        coalesce(
            ov.match_format,
            'best_of_' || nullif(m.raw_best_of, 0)::text
        ) as match_format,
        case
            when ov.match_format is not null then ov.best_of
            else nullif(m.raw_best_of, 0)
        end as best_of,
        ov.scheduled_games,
        coalesce(ov.scoring_rule, 'games_won') as scoring_rule,
        m.max_points_per_game,
        m.advantage_per_game,
        m.sudden_death_point,
        case
            when m.result_suffix is not null then m.result_suffix
            when
                m.overall_games_p1 is null
                or m.overall_games_p2 is null
                or (m.overall_games_p1 + m.overall_games_p2) = 0
                then 'NO_SCORE'
            when m.overall_games_p1 = m.overall_games_p2 then 'DRAW'
            else 'PLAYED'
        end as result_code,
        m.overall_score_raw,
        m.overall_games_p1,
        m.overall_games_p2,
        gt.derived_games_p1,
        gt.derived_games_p2,
        gt.points_won_p1 as raw_points_won_p1,
        gt.points_won_p2 as raw_points_won_p2,
        coalesce(gt.invalid_game_count, 0) > 0 as has_invalid_game_score,
        m.p1_player_id,
        m.p1_player_org,
        m.p2_player_id,
        m.p2_player_org,
        m.p1_timeout,
        m.p2_timeout,
        m.p1_card_y,
        m.p2_card_y,
        m.p1_card_yr1,
        m.p2_card_yr1,
        m.p1_card_yr2,
        m.p2_card_yr2
    from with_score_overrides as m
    inner join {{ ref('round_codes') }} as rc
        on m.raw_round_token = rc.raw_round_token
    left join
        lateral (
            select
                ov.match_format,
                ov.best_of,
                ov.scheduled_games,
                ov.scoring_rule
            from {{ ref('match_format_overrides') }} as ov
            where
                ov.event_id = m.event_id
                and (ov.document_code is null or ov.document_code = m.document_code)
                and (ov.discipline is null or ov.discipline = 'singles')
                and (ov.is_team_rubber is null or ov.is_team_rubber = m.is_team_rubber)
                and (ov.round_code is null or ov.round_code = rc.round_code)
            order by
                case when ov.document_code is not null then 0 else 1 end asc,
                case when ov.round_code is not null then 0 else 1 end asc,
                case when ov.is_team_rubber is not null then 0 else 1 end asc,
                case when ov.discipline is not null then 0 else 1 end asc,
                ov.override_id asc
            limit 1
        ) as ov
        on true
    left join game_totals as gt
        on
            m.event_id = gt.event_id
            and m.document_code = gt.document_code
),

classified as (
    select
        e.*,
        rt.result_type,
        rt.is_default_result,
        rt.is_rating_eligible as base_is_rating_eligible,
        rt.result_type <> 'no_score' as has_result,
        case
            when rt.result_type in ('draw', 'no_score') then null::smallint
            when e.overall_games_p1 > e.overall_games_p2 then 1::smallint
            when e.overall_games_p2 > e.overall_games_p1 then 2::smallint
        end as winner_side,
        case
            when rt.result_type not in ('played', 'draw') then false
            when
                coalesce(e.derived_games_p1, 0) <> e.overall_games_p1
                or coalesce(e.derived_games_p2, 0) <> e.overall_games_p2
                then true
            when e.match_format is null then true
            when e.match_format like 'best_of_%'
                then
                    e.best_of is null
                    or e.overall_games_p1 = e.overall_games_p2
                    or greatest(e.overall_games_p1, e.overall_games_p2)
                    <> ceil(e.best_of / 2.0)::integer
            when e.match_format = 'fixed_4_games'
                then (e.overall_games_p1 + e.overall_games_p2) <> 4
            when e.match_format = 'fixed_3_games'
                then (e.overall_games_p1 + e.overall_games_p2) not between 1 and 3
            else false
        end as has_score_anomaly
    from enriched as e
    inner join {{ ref('result_types') }} as rt
        on e.result_code = rt.result_code
)

select
    event_id,
    document_code,
    is_team_rubber,
    parent_document_code,
    rubber_number,
    p1_player_id,
    p1_player_org,
    p2_player_id,
    p2_player_org,
    gender_category,
    raw_round_token,
    round_code,
    round_stage,
    round_order,
    start_date_utc,
    start_date_local,
    match_format,
    best_of,
    scheduled_games,
    scoring_rule,
    max_points_per_game,
    advantage_per_game,
    sudden_death_point,
    result_code,
    result_type,
    is_default_result,
    has_result,
    winner_side,
    case
        when is_default_result and winner_side is not null
            then (3 - winner_side)::smallint
    end as defaulting_side,
    has_score_anomaly,
    has_invalid_game_score,
    base_is_rating_eligible and not has_score_anomaly as is_rating_eligible,
    overall_score_raw,
    case
        when result_type in ('played', 'draw') then overall_games_p1
    end as games_won_p1,
    case
        when result_type in ('played', 'draw') then overall_games_p2
    end as games_won_p2,
    case
        when result_type in ('played', 'draw') then raw_points_won_p1
    end as points_won_p1,
    case
        when result_type in ('played', 'draw') then raw_points_won_p2
    end as points_won_p2,
    derived_games_p1,
    derived_games_p2,
    p1_timeout,
    p2_timeout,
    p1_card_y,
    p2_card_y,
    p1_card_yr1,
    p2_card_yr1,
    p1_card_yr2,
    p2_card_yr2
from classified

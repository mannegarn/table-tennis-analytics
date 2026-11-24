{{ config(
    materialized='table',
    post_hook="analyze {{ this }}",
) }}

-- Extract individual team rubbers from teamParentData.extended_info.matches
-- in a single pass via jsonb_to_record on each match_result object.
-- Coalesces camelCase and PascalCase keys (event 3377 uses PascalCase inside
-- nested match_result objects).
with source as (
    select
        event_id,
        document_code,
        payload
    from {{ source('wtt', 'match_details_raw') }}
    where
        document_code ~ '^TTE[MWX]TEAM'
        and payload <> '{}'::jsonb
        and jsonb_typeof(payload) = 'object'
        and jsonb_typeof(
            payload -> 'teamParentData' -> 'extended_info' -> 'matches'
        ) = 'array'
),

rubber_elements as (
    select
        s.event_id,
        s.document_code as parent_document_code,
        {{ jsonb_text('m.value', 'value', 'Value') }} as elem_document_code,
        m.value -> 'match_result' as match_result
    from source as s
    cross join
        lateral jsonb_array_elements(
            s.payload -> 'teamParentData' -> 'extended_info' -> 'matches'
        ) as m
    where jsonb_typeof(m.value -> 'match_result') = 'object'
),

unpacked as (
    select
        re.event_id,
        re.parent_document_code,
        coalesce(
            nullif(btrim(p."documentCode"), ''),
            nullif(btrim(p."DocumentCode"), ''),
            re.elem_document_code
        ) as document_code,
        coalesce(
            nullif(btrim(p."subEventName"), ''),
            nullif(btrim(p."SubEventName"), '')
        ) as sub_event_name,
        coalesce(
            nullif(btrim(p."subEventDescription"), ''),
            nullif(btrim(p."SubEventDescription"), '')
        ) as sub_event_description,
        coalesce(
            nullif(btrim(p."venueName"), ''),
            nullif(btrim(p."VenueName"), '')
        ) as venue_name,
        coalesce(
            nullif(btrim(p."tableName"), ''),
            nullif(btrim(p."TableName"), '')
        ) as table_name,
        coalesce(
            nullif(btrim(p."tableNumber"), ''),
            nullif(btrim(p."TableNumber"), '')
        ) as table_number,
        coalesce(p."matchDateTime", p."MatchDateTime") as match_date_time,
        coalesce(p."matchConfig", p."MatchConfig") as match_config,
        coalesce(
            nullif(btrim(p."resultStatus"), ''),
            nullif(btrim(p."ResultStatus"), '')
        ) as result_status,
        coalesce(
            nullif(btrim(p."overallScores"), ''),
            nullif(btrim(p."OverallScores"), '')
        ) as overall_scores,
        coalesce(
            nullif(btrim(p."resultOverallScores"), ''),
            nullif(btrim(p."ResultOverallScores"), '')
        ) as result_overall_scores,
        coalesce(
            nullif(btrim(p."resultsGameScores"), ''),
            nullif(btrim(p."ResultsGameScores"), '')
        ) as results_game_scores,
        coalesce(p."currentGameNumber", p."CurrentGameNumber") as current_game_number_raw,
        coalesce(p."teamMatchNo", p."TeamMatchNo") as team_match_no_raw,
        coalesce(p.competitiors, p."Competitiors") -> 0 as comp_1,
        coalesce(p.competitiors, p."Competitiors") -> 1 as comp_2
    from rubber_elements as re
    cross join lateral jsonb_to_record(re.match_result) as p (
        "documentCode" text,
        "DocumentCode" text,
        "subEventName" text,
        "SubEventName" text,
        "subEventDescription" text,
        "SubEventDescription" text,
        "venueName" text,
        "VenueName" text,
        "tableName" text,
        "TableName" text,
        "tableNumber" text,
        "TableNumber" text,
        "matchDateTime" jsonb,
        "MatchDateTime" jsonb,
        "matchConfig" jsonb,
        "MatchConfig" jsonb,
        "resultStatus" text,
        "ResultStatus" text,
        "overallScores" text,
        "OverallScores" text,
        "resultOverallScores" text,
        "ResultOverallScores" text,
        "resultsGameScores" text,
        "ResultsGameScores" text,
        "currentGameNumber" text,
        "CurrentGameNumber" text,
        "teamMatchNo" text,
        "TeamMatchNo" text,
        competitiors jsonb,
        "Competitiors" jsonb
    )
),

prepared as (
    select
        *,
        {{ jsonb_text('match_date_time', 'startDateUTC', 'StartDateUTC') }}
            as start_date_utc_raw,
        {{ jsonb_text('match_date_time', 'startDateLocal', 'StartDateLocal') }}
            as start_date_local_raw,
        coalesce(comp_1 -> 'players', comp_1 -> 'Players') -> 0 as comp_1_p1,
        coalesce(comp_1 -> 'players', comp_1 -> 'Players') -> 1 as comp_1_p2,
        coalesce(comp_2 -> 'players', comp_2 -> 'Players') -> 0 as comp_2_p1,
        coalesce(comp_2 -> 'players', comp_2 -> 'Players') -> 1 as comp_2_p2,
        nullif(
            btrim(
                coalesce(
                    match_config ->> 'bestOfXGames',
                    match_config ->> 'BestOfXGames',
                    match_config ->> 'bestOf'
                )
            ),
            ''
        ) as best_of_raw,
        lower(
            nullif(
                btrim(
                    coalesce(
                        match_config ->> 'ttrReview',
                        match_config ->> 'tTRReview',
                        match_config ->> 'TTRReview'
                    )
                ),
                ''
            )
        ) as ttr_review_raw,
        regexp_match(
            overall_scores,
            '^([0-9]+)-([0-9]+)(?: ([A-Z]+))?$'
        ) as overall_score_parts,
        substring(document_code from 23 for 4) as round_token_pos23,
        substring(document_code from 27 for 4) as match_num_pos27
    from unpacked
),

parsed as (
    select
        event_id,
        document_code,
        parent_document_code,
        case
            when substring(document_code from 33 for 2) ~ '^[0-9]{2}$'
                then substring(document_code from 33 for 2)::smallint
        end as rubber_number,
        case
            when btrim(team_match_no_raw) ~ '^[0-9]+$'
                then btrim(team_match_no_raw)::integer
        end as team_match_no,
        case substring(document_code from 4 for 1)
            when 'M' then 'men'
            when 'W' then 'women'
            when 'X' then 'mixed'
        end as team_category,
        coalesce(
            'U' || (regexp_match(document_code, '^TTE[MWX]TEAM-+U([0-9]{2})'))[1],
            'senior'
        ) as age_category,
        case
            when substring(document_code from 16 for 4) ~ '^[MWX][SDT][0-9]+'
                then btrim(substring(document_code from 16 for 4), '-')
        end as para_class,
        case
            when round_token_pos23 ~ '^GP[0-9]{2}$' then 'GP##'
            when sub_event_description ilike 'stage 2 - group%' then 'GP##'
            when
                round_token_pos23 = 'FNL-'
                and sub_event_description ilike '%bronze%' then 'FNL2'
            else round_token_pos23
        end as raw_round_token,
        case
            when round_token_pos23 ~ '^GP[0-9]{2}$'
                then substring(round_token_pos23 from 3 for 2)::smallint
        end as group_number,
        case
            when match_num_pos27 ~ '^[0-9]{4}$'
                then match_num_pos27::integer
        end as match_number_in_round,
        sub_event_name,
        sub_event_description,
        venue_name,
        table_name,
        table_number,
        {{ jsonb_text('match_date_time', 'duration', 'Duration') }} as duration,
        {{ parse_wtt_timestamp_utc('start_date_utc_raw') }} as start_date_utc,
        {{ parse_wtt_timestamp_local('start_date_local_raw') }} as start_date_local,
        case
            when best_of_raw ~ '^[0-9]+$' then best_of_raw::integer
        end as best_of,
        {{ jsonb_cast_int('match_config', 'maxPointsPerGame', 'MaxPointsPerGame') }}
            as max_points_per_game,
        {{ jsonb_cast_int('match_config', 'advantagePerGame', 'AdvantagePerGame') }}
            as advantage_per_game,
        {{ jsonb_cast_int('match_config', 'suddenDeathPoint', 'SuddenDeathPoint') }}
            as sudden_death_point,
        {{ jsonb_cast_int(
            'match_config',
            'maxChallengesPerCompetitor',
            'MaxChallengesPerCompetitor'
        ) }} as max_challenges_per_competitor,
        case
            when ttr_review_raw in ('true', 'false') then ttr_review_raw::boolean
        end as ttr_review,
        result_status,
        overall_scores,
        overall_score_parts[1]::smallint as overall_games_p1,
        overall_score_parts[2]::smallint as overall_games_p2,
        overall_score_parts[3] as result_suffix,
        result_overall_scores,
        results_game_scores,
        case
            when btrim(current_game_number_raw) ~ '^[0-9]+$'
                then btrim(current_game_number_raw)::integer
        end as current_game_number,
        {{ jsonb_text('comp_1', 'competitiorId', 'CompetitiorId') }} as comp_1_id,
        {{ jsonb_text('comp_1', 'competitiorName', 'CompetitiorName') }} as comp_1_name,
        {{ jsonb_text('comp_1', 'competitiorOrg', 'CompetitiorOrg') }} as comp_1_org,
        {{ jsonb_text('comp_1', 'competitorType', 'CompetitorType') }} as comp_1_type,
        {{ jsonb_text('comp_1', 'scores', 'Scores') }} as comp_1_scores,
        {{ jsonb_text('comp_1', 'irm', 'Irm') }} as comp_1_irm,
        {{ jsonb_cast_int('comp_1', 'compTimeout', 'CompTimeout') }} as comp_1_timeout,
        {{ jsonb_cast_int('comp_1', 'compMedicalTimeout', 'CompMedicalTimeout') }}
            as comp_1_medical_timeout,
        {{ jsonb_cast_int('comp_1', 'compCardY', 'CompCardY') }} as comp_1_card_y,
        {{ jsonb_cast_int('comp_1', 'compCardYR1', 'CompCardYR1') }} as comp_1_card_yr1,
        {{ jsonb_cast_int('comp_1', 'compCardYR2', 'CompCardYR2') }} as comp_1_card_yr2,
        {{ jsonb_cast_int('comp_1_p1', 'playerId', 'PlayerId', 'bigint') }}
            as comp_1_player_1_id,
        {{ jsonb_text('comp_1_p1', 'playerName', 'PlayerName') }} as comp_1_player_1_name,
        {{ jsonb_text('comp_1_p1', 'playerOrgCode', 'PlayerOrgCode') }} as comp_1_player_1_org,
        {{ jsonb_cast_int('comp_1_p1', 'playerPosition', 'PlayerPosition') }}
            as comp_1_player_1_position,
        {{ jsonb_cast_int('comp_1_p2', 'playerId', 'PlayerId', 'bigint') }}
            as comp_1_player_2_id,
        {{ jsonb_text('comp_1_p2', 'playerName', 'PlayerName') }} as comp_1_player_2_name,
        {{ jsonb_text('comp_1_p2', 'playerOrgCode', 'PlayerOrgCode') }} as comp_1_player_2_org,
        {{ jsonb_cast_int('comp_1_p2', 'playerPosition', 'PlayerPosition') }}
            as comp_1_player_2_position,
        {{ jsonb_text('comp_2', 'competitiorId', 'CompetitiorId') }} as comp_2_id,
        {{ jsonb_text('comp_2', 'competitiorName', 'CompetitiorName') }} as comp_2_name,
        {{ jsonb_text('comp_2', 'competitiorOrg', 'CompetitiorOrg') }} as comp_2_org,
        {{ jsonb_text('comp_2', 'competitorType', 'CompetitorType') }} as comp_2_type,
        {{ jsonb_text('comp_2', 'scores', 'Scores') }} as comp_2_scores,
        {{ jsonb_text('comp_2', 'irm', 'Irm') }} as comp_2_irm,
        {{ jsonb_cast_int('comp_2', 'compTimeout', 'CompTimeout') }} as comp_2_timeout,
        {{ jsonb_cast_int('comp_2', 'compMedicalTimeout', 'CompMedicalTimeout') }}
            as comp_2_medical_timeout,
        {{ jsonb_cast_int('comp_2', 'compCardY', 'CompCardY') }} as comp_2_card_y,
        {{ jsonb_cast_int('comp_2', 'compCardYR1', 'CompCardYR1') }} as comp_2_card_yr1,
        {{ jsonb_cast_int('comp_2', 'compCardYR2', 'CompCardYR2') }} as comp_2_card_yr2,
        {{ jsonb_cast_int('comp_2_p1', 'playerId', 'PlayerId', 'bigint') }}
            as comp_2_player_1_id,
        {{ jsonb_text('comp_2_p1', 'playerName', 'PlayerName') }} as comp_2_player_1_name,
        {{ jsonb_text('comp_2_p1', 'playerOrgCode', 'PlayerOrgCode') }} as comp_2_player_1_org,
        {{ jsonb_cast_int('comp_2_p1', 'playerPosition', 'PlayerPosition') }}
            as comp_2_player_1_position,
        {{ jsonb_cast_int('comp_2_p2', 'playerId', 'PlayerId', 'bigint') }}
            as comp_2_player_2_id,
        {{ jsonb_text('comp_2_p2', 'playerName', 'PlayerName') }} as comp_2_player_2_name,
        {{ jsonb_text('comp_2_p2', 'playerOrgCode', 'PlayerOrgCode') }} as comp_2_player_2_org,
        {{ jsonb_cast_int('comp_2_p2', 'playerPosition', 'PlayerPosition') }}
            as comp_2_player_2_position
    from prepared
)

select
    event_id,
    document_code,
    parent_document_code,
    rubber_number,
    team_match_no,
    team_category,
    case
        when team_category = 'men' then 'men'
        when team_category = 'women' then 'women'
        when team_category = 'mixed' and rubber_number = 1 then 'mixed'
        when team_category = 'mixed' and rubber_number = 2 then 'women'
        when team_category = 'mixed' and rubber_number = 3 then 'men'
    end as gender_category,
    case
        when comp_1_player_2_id is null and comp_2_player_2_id is null then 'singles'
        when team_category = 'mixed' and rubber_number = 1 then 'mixed_doubles'
        else 'doubles'
    end as discipline,
    age_category,
    age_category <> 'senior' as is_youth,
    para_class,
    para_class is not null as is_para,
    raw_round_token,
    group_number,
    match_number_in_round,
    sub_event_name,
    sub_event_description,
    venue_name,
    table_name,
    table_number,
    duration,
    start_date_utc,
    start_date_local,
    best_of,
    max_points_per_game,
    advantage_per_game,
    sudden_death_point,
    max_challenges_per_competitor,
    ttr_review,
    result_status,
    overall_scores,
    overall_games_p1,
    overall_games_p2,
    result_suffix,
    result_overall_scores,
    results_game_scores,
    current_game_number,
    comp_1_id,
    comp_1_name,
    comp_1_org,
    comp_1_type,
    comp_1_scores,
    comp_1_irm,
    comp_1_timeout,
    comp_1_medical_timeout,
    comp_1_card_y,
    comp_1_card_yr1,
    comp_1_card_yr2,
    comp_1_player_1_id,
    comp_1_player_1_name,
    comp_1_player_1_org,
    comp_1_player_1_position,
    comp_1_player_2_id,
    comp_1_player_2_name,
    comp_1_player_2_org,
    comp_1_player_2_position,
    comp_2_id,
    comp_2_name,
    comp_2_org,
    comp_2_type,
    comp_2_scores,
    comp_2_irm,
    comp_2_timeout,
    comp_2_medical_timeout,
    comp_2_card_y,
    comp_2_card_yr1,
    comp_2_card_yr2,
    comp_2_player_1_id,
    comp_2_player_1_name,
    comp_2_player_1_org,
    comp_2_player_1_position,
    comp_2_player_2_id,
    comp_2_player_2_name,
    comp_2_player_2_org,
    comp_2_player_2_position
from parsed

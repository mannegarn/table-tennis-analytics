{{ config(materialized='table') }}

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
        coalesce(
            nullif(btrim(m.value ->> 'value'), ''),
            nullif(btrim(m.value ->> 'Value'), '')
        ) as elem_document_code,
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
        nullif(
            btrim(coalesce(match_date_time ->> 'startDateUTC', match_date_time ->> 'StartDateUTC')),
            ''
        ) as start_date_utc_raw,
        nullif(
            btrim(
                coalesce(
                    match_date_time ->> 'startDateLocal',
                    match_date_time ->> 'StartDateLocal'
                )
            ),
            ''
        ) as start_date_local_raw,
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
        nullif(
            btrim(
                coalesce(
                    match_config ->> 'maxPointsPerGame',
                    match_config ->> 'MaxPointsPerGame'
                )
            ),
            ''
        ) as max_points_per_game_raw,
        nullif(
            btrim(
                coalesce(
                    match_config ->> 'advantagePerGame',
                    match_config ->> 'AdvantagePerGame'
                )
            ),
            ''
        ) as advantage_per_game_raw,
        nullif(
            btrim(
                coalesce(
                    match_config ->> 'suddenDeathPoint',
                    match_config ->> 'SuddenDeathPoint'
                )
            ),
            ''
        ) as sudden_death_point_raw,
        nullif(
            btrim(
                coalesce(
                    match_config ->> 'maxChallengesPerCompetitor',
                    match_config ->> 'MaxChallengesPerCompetitor'
                )
            ),
            ''
        ) as max_challenges_raw,
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
        ) as ttr_review_raw
    from unpacked
),

parsed as (
    select
        event_id,
        document_code,
        parent_document_code,
        substring(document_code from 33 for 2)::smallint as rubber_number,
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
        (
            regexp_match(document_code, '^TTE[MWX]TEAM-+U([0-9]{2})')
        )[1] is not null as is_youth,
        case
            when substring(document_code from 23 for 4) ~ '^GP[0-9]{2}$' then 'GP##'
            when
                substring(document_code from 23 for 4) = 'FNL-'
                and sub_event_description ilike '%bronze%' then 'FNL2'
            else substring(document_code from 23 for 4)
        end as raw_round_token,
        sub_event_name,
        sub_event_description,
        venue_name,
        table_name,
        table_number,
        nullif(
            btrim(coalesce(match_date_time ->> 'duration', match_date_time ->> 'Duration')),
            ''
        ) as duration,
        case
            when
                start_date_utc_raw is not null
                and start_date_utc_raw not like '01/01/0001%'
                then
                    to_timestamp(start_date_utc_raw, 'MM/DD/YYYY HH24:MI:SS')
                    at time zone 'UTC'
        end as start_date_utc,
        case
            when
                start_date_local_raw is not null
                and start_date_local_raw not like '01/01/0001%'
                then to_timestamp(start_date_local_raw, 'MM/DD/YYYY HH24:MI:SS')::timestamp
        end as start_date_local,
        case
            when best_of_raw ~ '^[0-9]+$' then best_of_raw::integer
        end as best_of,
        case
            when max_points_per_game_raw ~ '^[0-9]+$' then max_points_per_game_raw::integer
        end as max_points_per_game,
        case
            when advantage_per_game_raw ~ '^[0-9]+$' then advantage_per_game_raw::integer
        end as advantage_per_game,
        case
            when sudden_death_point_raw ~ '^[0-9]+$' then sudden_death_point_raw::integer
        end as sudden_death_point,
        case
            when max_challenges_raw ~ '^[0-9]+$' then max_challenges_raw::integer
        end as max_challenges_per_competitor,
        case
            when ttr_review_raw in ('true', 'false') then ttr_review_raw::boolean
        end as ttr_review,
        result_status,
        overall_scores,
        result_overall_scores,
        results_game_scores,
        case
            when btrim(current_game_number_raw) ~ '^[0-9]+$'
                then btrim(current_game_number_raw)::integer
        end as current_game_number,
        nullif(
            btrim(coalesce(comp_1 ->> 'competitiorId', comp_1 ->> 'CompetitiorId')),
            ''
        ) as comp_1_id,
        nullif(
            btrim(coalesce(comp_1 ->> 'competitiorName', comp_1 ->> 'CompetitiorName')),
            ''
        ) as comp_1_name,
        nullif(
            btrim(coalesce(comp_1 ->> 'competitiorOrg', comp_1 ->> 'CompetitiorOrg')),
            ''
        ) as comp_1_org,
        nullif(
            btrim(coalesce(comp_1 ->> 'competitorType', comp_1 ->> 'CompetitorType')),
            ''
        ) as comp_1_type,
        nullif(btrim(coalesce(comp_1 ->> 'scores', comp_1 ->> 'Scores')), '') as comp_1_scores,
        nullif(btrim(coalesce(comp_1 ->> 'irm', comp_1 ->> 'Irm')), '') as comp_1_irm,
        case
            when btrim(coalesce(comp_1 ->> 'compTimeout', comp_1 ->> 'CompTimeout')) ~ '^[0-9]+$'
                then btrim(coalesce(comp_1 ->> 'compTimeout', comp_1 ->> 'CompTimeout'))::integer
        end as comp_1_timeout,
        case
            when
                btrim(
                    coalesce(
                        comp_1 ->> 'compMedicalTimeout',
                        comp_1 ->> 'CompMedicalTimeout'
                    )
                ) ~ '^[0-9]+$'
                then btrim(
                    coalesce(
                        comp_1 ->> 'compMedicalTimeout',
                        comp_1 ->> 'CompMedicalTimeout'
                    )
                )::integer
        end as comp_1_medical_timeout,
        case
            when btrim(coalesce(comp_1 ->> 'compCardY', comp_1 ->> 'CompCardY')) ~ '^[0-9]+$'
                then btrim(coalesce(comp_1 ->> 'compCardY', comp_1 ->> 'CompCardY'))::integer
        end as comp_1_card_y,
        case
            when btrim(coalesce(comp_1 ->> 'compCardYR1', comp_1 ->> 'CompCardYR1')) ~ '^[0-9]+$'
                then btrim(coalesce(comp_1 ->> 'compCardYR1', comp_1 ->> 'CompCardYR1'))::integer
        end as comp_1_card_yr1,
        case
            when btrim(coalesce(comp_1 ->> 'compCardYR2', comp_1 ->> 'CompCardYR2')) ~ '^[0-9]+$'
                then btrim(coalesce(comp_1 ->> 'compCardYR2', comp_1 ->> 'CompCardYR2'))::integer
        end as comp_1_card_yr2,
        case
            when btrim(coalesce(comp_1_p1 ->> 'playerId', comp_1_p1 ->> 'PlayerId')) ~ '^[0-9]+$'
                then btrim(coalesce(comp_1_p1 ->> 'playerId', comp_1_p1 ->> 'PlayerId'))::bigint
        end as comp_1_player_1_id,
        nullif(
            btrim(coalesce(comp_1_p1 ->> 'playerName', comp_1_p1 ->> 'PlayerName')),
            ''
        ) as comp_1_player_1_name,
        nullif(
            btrim(coalesce(comp_1_p1 ->> 'playerOrgCode', comp_1_p1 ->> 'PlayerOrgCode')),
            ''
        ) as comp_1_player_1_org,
        case
            when
                btrim(
                    coalesce(comp_1_p1 ->> 'playerPosition', comp_1_p1 ->> 'PlayerPosition')
                ) ~ '^[0-9]+$'
                then btrim(
                    coalesce(comp_1_p1 ->> 'playerPosition', comp_1_p1 ->> 'PlayerPosition')
                )::integer
        end as comp_1_player_1_position,
        case
            when btrim(coalesce(comp_1_p2 ->> 'playerId', comp_1_p2 ->> 'PlayerId')) ~ '^[0-9]+$'
                then btrim(coalesce(comp_1_p2 ->> 'playerId', comp_1_p2 ->> 'PlayerId'))::bigint
        end as comp_1_player_2_id,
        nullif(
            btrim(coalesce(comp_1_p2 ->> 'playerName', comp_1_p2 ->> 'PlayerName')),
            ''
        ) as comp_1_player_2_name,
        nullif(
            btrim(coalesce(comp_1_p2 ->> 'playerOrgCode', comp_1_p2 ->> 'PlayerOrgCode')),
            ''
        ) as comp_1_player_2_org,
        case
            when
                btrim(
                    coalesce(comp_1_p2 ->> 'playerPosition', comp_1_p2 ->> 'PlayerPosition')
                ) ~ '^[0-9]+$'
                then btrim(
                    coalesce(comp_1_p2 ->> 'playerPosition', comp_1_p2 ->> 'PlayerPosition')
                )::integer
        end as comp_1_player_2_position,
        nullif(
            btrim(coalesce(comp_2 ->> 'competitiorId', comp_2 ->> 'CompetitiorId')),
            ''
        ) as comp_2_id,
        nullif(
            btrim(coalesce(comp_2 ->> 'competitiorName', comp_2 ->> 'CompetitiorName')),
            ''
        ) as comp_2_name,
        nullif(
            btrim(coalesce(comp_2 ->> 'competitiorOrg', comp_2 ->> 'CompetitiorOrg')),
            ''
        ) as comp_2_org,
        nullif(
            btrim(coalesce(comp_2 ->> 'competitorType', comp_2 ->> 'CompetitorType')),
            ''
        ) as comp_2_type,
        nullif(btrim(coalesce(comp_2 ->> 'scores', comp_2 ->> 'Scores')), '') as comp_2_scores,
        nullif(btrim(coalesce(comp_2 ->> 'irm', comp_2 ->> 'Irm')), '') as comp_2_irm,
        case
            when btrim(coalesce(comp_2 ->> 'compTimeout', comp_2 ->> 'CompTimeout')) ~ '^[0-9]+$'
                then btrim(coalesce(comp_2 ->> 'compTimeout', comp_2 ->> 'CompTimeout'))::integer
        end as comp_2_timeout,
        case
            when
                btrim(
                    coalesce(
                        comp_2 ->> 'compMedicalTimeout',
                        comp_2 ->> 'CompMedicalTimeout'
                    )
                ) ~ '^[0-9]+$'
                then btrim(
                    coalesce(
                        comp_2 ->> 'compMedicalTimeout',
                        comp_2 ->> 'CompMedicalTimeout'
                    )
                )::integer
        end as comp_2_medical_timeout,
        case
            when btrim(coalesce(comp_2 ->> 'compCardY', comp_2 ->> 'CompCardY')) ~ '^[0-9]+$'
                then btrim(coalesce(comp_2 ->> 'compCardY', comp_2 ->> 'CompCardY'))::integer
        end as comp_2_card_y,
        case
            when btrim(coalesce(comp_2 ->> 'compCardYR1', comp_2 ->> 'CompCardYR1')) ~ '^[0-9]+$'
                then btrim(coalesce(comp_2 ->> 'compCardYR1', comp_2 ->> 'CompCardYR1'))::integer
        end as comp_2_card_yr1,
        case
            when btrim(coalesce(comp_2 ->> 'compCardYR2', comp_2 ->> 'CompCardYR2')) ~ '^[0-9]+$'
                then btrim(coalesce(comp_2 ->> 'compCardYR2', comp_2 ->> 'CompCardYR2'))::integer
        end as comp_2_card_yr2,
        case
            when btrim(coalesce(comp_2_p1 ->> 'playerId', comp_2_p1 ->> 'PlayerId')) ~ '^[0-9]+$'
                then btrim(coalesce(comp_2_p1 ->> 'playerId', comp_2_p1 ->> 'PlayerId'))::bigint
        end as comp_2_player_1_id,
        nullif(
            btrim(coalesce(comp_2_p1 ->> 'playerName', comp_2_p1 ->> 'PlayerName')),
            ''
        ) as comp_2_player_1_name,
        nullif(
            btrim(coalesce(comp_2_p1 ->> 'playerOrgCode', comp_2_p1 ->> 'PlayerOrgCode')),
            ''
        ) as comp_2_player_1_org,
        case
            when
                btrim(
                    coalesce(comp_2_p1 ->> 'playerPosition', comp_2_p1 ->> 'PlayerPosition')
                ) ~ '^[0-9]+$'
                then btrim(
                    coalesce(comp_2_p1 ->> 'playerPosition', comp_2_p1 ->> 'PlayerPosition')
                )::integer
        end as comp_2_player_1_position,
        case
            when btrim(coalesce(comp_2_p2 ->> 'playerId', comp_2_p2 ->> 'PlayerId')) ~ '^[0-9]+$'
                then btrim(coalesce(comp_2_p2 ->> 'playerId', comp_2_p2 ->> 'PlayerId'))::bigint
        end as comp_2_player_2_id,
        nullif(
            btrim(coalesce(comp_2_p2 ->> 'playerName', comp_2_p2 ->> 'PlayerName')),
            ''
        ) as comp_2_player_2_name,
        nullif(
            btrim(coalesce(comp_2_p2 ->> 'playerOrgCode', comp_2_p2 ->> 'PlayerOrgCode')),
            ''
        ) as comp_2_player_2_org,
        case
            when
                btrim(
                    coalesce(comp_2_p2 ->> 'playerPosition', comp_2_p2 ->> 'PlayerPosition')
                ) ~ '^[0-9]+$'
                then btrim(
                    coalesce(comp_2_p2 ->> 'playerPosition', comp_2_p2 ->> 'PlayerPosition')
                )::integer
        end as comp_2_player_2_position
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
        when comp_1_player_2_id is null and comp_2_player_2_id is null then 'singles'
        when team_category = 'mixed' and rubber_number = 1 then 'mixed_doubles'
        else 'doubles'
    end as discipline,
    age_category,
    is_youth,
    raw_round_token,
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

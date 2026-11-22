{{ config(materialized='table') }}

-- Extract the top-level fields from the TOASTed payload in a single pass via
-- jsonb_to_record so PostgreSQL detoasts and decompresses each ~55 KB payload
-- once per row instead of once per JSONB operator.
with source as (
    select
        event_id,
        document_code,
        payload
    from {{ source('wtt', 'match_details_raw') }}
    where
        payload <> '{}'::jsonb
        and jsonb_typeof(payload) = 'object'
),

unpacked as (
    select
        s.event_id,
        s.document_code,
        p."subEventName" as sub_event_name_raw,
        p."subEventDescription" as sub_event_description_raw,
        p."venueName" as venue_name_raw,
        p."tableName" as table_name_raw,
        p."tableNumber" as table_number_raw,
        p."matchDateTime" as match_date_time,
        p."matchConfig" as match_config,
        p."resultStatus" as result_status_raw,
        p."overallScores" as overall_scores_raw,
        p."resultOverallScores" as result_overall_scores_raw,
        p."resultsGameScores" as results_game_scores_raw,
        p."currentGameNumber" as current_game_number_raw,
        p.competitiors -> 0 as comp_1,
        p.competitiors -> 1 as comp_2
    from source as s
    cross join lateral jsonb_to_record(s.payload) as p (
        "subEventName" text,
        "subEventDescription" text,
        "venueName" text,
        "tableName" text,
        "tableNumber" text,
        "matchDateTime" jsonb,
        "matchConfig" jsonb,
        "resultStatus" text,
        "overallScores" text,
        "resultOverallScores" text,
        "resultsGameScores" text,
        "currentGameNumber" text,
        competitiors jsonb
    )
)

select
    event_id,
    document_code,
    nullif(btrim(sub_event_name_raw), '') as sub_event_name,
    nullif(btrim(sub_event_description_raw), '') as sub_event_description,
    nullif(btrim(venue_name_raw), '') as venue_name,
    nullif(btrim(table_name_raw), '') as table_name,
    nullif(btrim(table_number_raw), '') as table_number,
    nullif(btrim(match_date_time ->> 'duration'), '') as duration,
    to_timestamp(
        nullif(btrim(match_date_time ->> 'startDateUTC'), ''),
        'MM/DD/YYYY HH24:MI:SS'
    ) at time zone 'UTC' as start_date_utc,
    to_timestamp(
        nullif(btrim(match_date_time ->> 'startDateLocal'), ''),
        'MM/DD/YYYY HH24:MI:SS'
    )::timestamp as start_date_local,
    case
        when
            coalesce(
                match_config ->> 'bestOfXGames',
                match_config ->> 'bestOf'
            ) ~ '^[0-9]+$'
            then coalesce(
                match_config ->> 'bestOfXGames',
                match_config ->> 'bestOf'
            )::integer
    end as best_of,
    case
        when (match_config ->> 'maxChallengesPerCompetitor') ~ '^[0-9]+$'
            then (match_config ->> 'maxChallengesPerCompetitor')::integer
    end as max_challenges_per_competitor,
    case
        when
            lower(
                coalesce(
                    match_config ->> 'ttrReview',
                    match_config ->> 'tTRReview'
                )
            ) in ('true', 'false')
            then lower(
                coalesce(
                    match_config ->> 'ttrReview',
                    match_config ->> 'tTRReview'
                )
            )::boolean
    end as ttr_review,
    nullif(btrim(result_status_raw), '') as result_status,
    nullif(btrim(overall_scores_raw), '') as overall_scores,
    nullif(btrim(result_overall_scores_raw), '') as result_overall_scores,
    nullif(btrim(results_game_scores_raw), '') as results_game_scores,
    case
        when current_game_number_raw ~ '^[0-9]+$'
            then current_game_number_raw::integer
    end as current_game_number,
    nullif(btrim(comp_1 ->> 'competitiorId'), '') as comp_1_id,
    nullif(btrim(comp_1 ->> 'competitiorName'), '') as comp_1_name,
    nullif(btrim(comp_1 ->> 'competitiorOrg'), '') as comp_1_org,
    nullif(btrim(comp_1 ->> 'competitorType'), '') as comp_1_type,
    nullif(btrim(comp_1 ->> 'scores'), '') as comp_1_scores,
    nullif(btrim(comp_1 ->> 'irm'), '') as comp_1_irm,
    case
        when btrim(comp_1 ->> 'compTimeout') ~ '^[0-9]+$'
            then btrim(comp_1 ->> 'compTimeout')::integer
    end as comp_1_timeout,
    case
        when btrim(comp_1 ->> 'compMedicalTimeout') ~ '^[0-9]+$'
            then btrim(comp_1 ->> 'compMedicalTimeout')::integer
    end as comp_1_medical_timeout,
    case
        when btrim(comp_1 ->> 'compCardY') ~ '^[0-9]+$'
            then btrim(comp_1 ->> 'compCardY')::integer
    end as comp_1_card_y,
    case
        when btrim(comp_1 ->> 'compCardYR1') ~ '^[0-9]+$'
            then btrim(comp_1 ->> 'compCardYR1')::integer
    end as comp_1_card_yr1,
    case
        when btrim(comp_1 ->> 'compCardYR2') ~ '^[0-9]+$'
            then btrim(comp_1 ->> 'compCardYR2')::integer
    end as comp_1_card_yr2,
    case
        when (comp_1 -> 'players' -> 0 ->> 'playerId') ~ '^[0-9]+$'
            then (comp_1 -> 'players' -> 0 ->> 'playerId')::bigint
    end as comp_1_player_1_id,
    nullif(
        btrim(comp_1 -> 'players' -> 0 ->> 'playerName'), ''
    ) as comp_1_player_1_name,
    nullif(
        btrim(comp_1 -> 'players' -> 0 ->> 'playerOrgCode'), ''
    ) as comp_1_player_1_org,
    case
        when (comp_1 -> 'players' -> 0 ->> 'playerPosition') ~ '^[0-9]+$'
            then (comp_1 -> 'players' -> 0 ->> 'playerPosition')::integer
    end as comp_1_player_1_position,
    case
        when (comp_1 -> 'players' -> 1 ->> 'playerId') ~ '^[0-9]+$'
            then (comp_1 -> 'players' -> 1 ->> 'playerId')::bigint
    end as comp_1_player_2_id,
    nullif(
        btrim(comp_1 -> 'players' -> 1 ->> 'playerName'), ''
    ) as comp_1_player_2_name,
    nullif(
        btrim(comp_1 -> 'players' -> 1 ->> 'playerOrgCode'), ''
    ) as comp_1_player_2_org,
    case
        when (comp_1 -> 'players' -> 1 ->> 'playerPosition') ~ '^[0-9]+$'
            then (comp_1 -> 'players' -> 1 ->> 'playerPosition')::integer
    end as comp_1_player_2_position,
    nullif(btrim(comp_2 ->> 'competitiorId'), '') as comp_2_id,
    nullif(btrim(comp_2 ->> 'competitiorName'), '') as comp_2_name,
    nullif(btrim(comp_2 ->> 'competitiorOrg'), '') as comp_2_org,
    nullif(btrim(comp_2 ->> 'competitorType'), '') as comp_2_type,
    nullif(btrim(comp_2 ->> 'scores'), '') as comp_2_scores,
    nullif(btrim(comp_2 ->> 'irm'), '') as comp_2_irm,
    case
        when btrim(comp_2 ->> 'compTimeout') ~ '^[0-9]+$'
            then btrim(comp_2 ->> 'compTimeout')::integer
    end as comp_2_timeout,
    case
        when btrim(comp_2 ->> 'compMedicalTimeout') ~ '^[0-9]+$'
            then btrim(comp_2 ->> 'compMedicalTimeout')::integer
    end as comp_2_medical_timeout,
    case
        when btrim(comp_2 ->> 'compCardY') ~ '^[0-9]+$'
            then btrim(comp_2 ->> 'compCardY')::integer
    end as comp_2_card_y,
    case
        when btrim(comp_2 ->> 'compCardYR1') ~ '^[0-9]+$'
            then btrim(comp_2 ->> 'compCardYR1')::integer
    end as comp_2_card_yr1,
    case
        when btrim(comp_2 ->> 'compCardYR2') ~ '^[0-9]+$'
            then btrim(comp_2 ->> 'compCardYR2')::integer
    end as comp_2_card_yr2,
    case
        when (comp_2 -> 'players' -> 0 ->> 'playerId') ~ '^[0-9]+$'
            then (comp_2 -> 'players' -> 0 ->> 'playerId')::bigint
    end as comp_2_player_1_id,
    nullif(
        btrim(comp_2 -> 'players' -> 0 ->> 'playerName'), ''
    ) as comp_2_player_1_name,
    nullif(
        btrim(comp_2 -> 'players' -> 0 ->> 'playerOrgCode'), ''
    ) as comp_2_player_1_org,
    case
        when (comp_2 -> 'players' -> 0 ->> 'playerPosition') ~ '^[0-9]+$'
            then (comp_2 -> 'players' -> 0 ->> 'playerPosition')::integer
    end as comp_2_player_1_position,
    case
        when (comp_2 -> 'players' -> 1 ->> 'playerId') ~ '^[0-9]+$'
            then (comp_2 -> 'players' -> 1 ->> 'playerId')::bigint
    end as comp_2_player_2_id,
    nullif(
        btrim(comp_2 -> 'players' -> 1 ->> 'playerName'), ''
    ) as comp_2_player_2_name,
    nullif(
        btrim(comp_2 -> 'players' -> 1 ->> 'playerOrgCode'), ''
    ) as comp_2_player_2_org,
    case
        when (comp_2 -> 'players' -> 1 ->> 'playerPosition') ~ '^[0-9]+$'
            then (comp_2 -> 'players' -> 1 ->> 'playerPosition')::integer
    end as comp_2_player_2_position
from unpacked

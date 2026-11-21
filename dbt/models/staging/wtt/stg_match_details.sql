{{ config(materialized='table') }}

with source as (
    select
        event_id,
        document_code,
        payload
    from {{ source('wtt', 'match_details_raw') }}
    where
        payload <> '{}'::jsonb
        and jsonb_typeof(payload) = 'object'
)

select
    event_id,
    document_code,
    nullif(btrim(payload ->> 'subEventName'), '') as sub_event_name,
    nullif(btrim(payload ->> 'subEventDescription'), '') as sub_event_description,
    nullif(btrim(payload ->> 'venueName'), '') as venue_name,
    nullif(btrim(payload ->> 'tableName'), '') as table_name,
    nullif(btrim(payload ->> 'tableNumber'), '') as table_number,
    nullif(btrim(payload -> 'matchDateTime' ->> 'duration'), '') as duration,
    to_timestamp(
        nullif(btrim(payload -> 'matchDateTime' ->> 'startDateUTC'), ''),
        'MM/DD/YYYY HH24:MI:SS'
    ) at time zone 'UTC' as start_date_utc,
    to_timestamp(
        nullif(btrim(payload -> 'matchDateTime' ->> 'startDateLocal'), ''),
        'MM/DD/YYYY HH24:MI:SS'
    )::timestamp as start_date_local,
    case
        when
            coalesce(
                payload -> 'matchConfig' ->> 'bestOfXGames',
                payload -> 'matchConfig' ->> 'bestOf'
            ) ~ '^[0-9]+$'
            then coalesce(
                payload -> 'matchConfig' ->> 'bestOfXGames',
                payload -> 'matchConfig' ->> 'bestOf'
            )::integer
    end as best_of,
    case
        when (payload -> 'matchConfig' ->> 'maxChallengesPerCompetitor') ~ '^[0-9]+$'
            then (payload -> 'matchConfig' ->> 'maxChallengesPerCompetitor')::integer
    end as max_challenges_per_competitor,
    case
        when
            lower(
                coalesce(
                    payload -> 'matchConfig' ->> 'ttrReview',
                    payload -> 'matchConfig' ->> 'tTRReview'
                )
            ) in ('true', 'false')
            then lower(
                coalesce(
                    payload -> 'matchConfig' ->> 'ttrReview',
                    payload -> 'matchConfig' ->> 'tTRReview'
                )
            )::boolean
    end as ttr_review,
    nullif(btrim(payload ->> 'resultStatus'), '') as result_status,
    nullif(btrim(payload ->> 'overallScores'), '') as overall_scores,
    nullif(btrim(payload ->> 'resultOverallScores'), '') as result_overall_scores,
    nullif(btrim(payload ->> 'resultsGameScores'), '') as results_game_scores,
    case
        when (payload ->> 'currentGameNumber') ~ '^[0-9]+$'
            then (payload ->> 'currentGameNumber')::integer
    end as current_game_number,
    nullif(btrim(payload -> 'competitiors' -> 0 ->> 'competitiorId'), '') as comp_1_id,
    nullif(btrim(payload -> 'competitiors' -> 0 ->> 'competitiorName'), '') as comp_1_name,
    nullif(btrim(payload -> 'competitiors' -> 0 ->> 'competitiorOrg'), '') as comp_1_org,
    nullif(btrim(payload -> 'competitiors' -> 0 ->> 'competitorType'), '') as comp_1_type,
    nullif(btrim(payload -> 'competitiors' -> 0 ->> 'scores'), '') as comp_1_scores,
    nullif(btrim(payload -> 'competitiors' -> 0 ->> 'irm'), '') as comp_1_irm,
    case
        when btrim(payload -> 'competitiors' -> 0 ->> 'compTimeout') ~ '^[0-9]+$'
            then btrim(payload -> 'competitiors' -> 0 ->> 'compTimeout')::integer
    end as comp_1_timeout,
    case
        when btrim(payload -> 'competitiors' -> 0 ->> 'compMedicalTimeout') ~ '^[0-9]+$'
            then btrim(payload -> 'competitiors' -> 0 ->> 'compMedicalTimeout')::integer
    end as comp_1_medical_timeout,
    case
        when btrim(payload -> 'competitiors' -> 0 ->> 'compCardY') ~ '^[0-9]+$'
            then btrim(payload -> 'competitiors' -> 0 ->> 'compCardY')::integer
    end as comp_1_card_y,
    case
        when btrim(payload -> 'competitiors' -> 0 ->> 'compCardYR1') ~ '^[0-9]+$'
            then btrim(payload -> 'competitiors' -> 0 ->> 'compCardYR1')::integer
    end as comp_1_card_yr1,
    case
        when btrim(payload -> 'competitiors' -> 0 ->> 'compCardYR2') ~ '^[0-9]+$'
            then btrim(payload -> 'competitiors' -> 0 ->> 'compCardYR2')::integer
    end as comp_1_card_yr2,
    case
        when (payload -> 'competitiors' -> 0 -> 'players' -> 0 ->> 'playerId') ~ '^[0-9]+$'
            then (payload -> 'competitiors' -> 0 -> 'players' -> 0 ->> 'playerId')::bigint
    end as comp_1_player_1_id,
    nullif(
        btrim(payload -> 'competitiors' -> 0 -> 'players' -> 0 ->> 'playerName'), ''
    ) as comp_1_player_1_name,
    nullif(
        btrim(payload -> 'competitiors' -> 0 -> 'players' -> 0 ->> 'playerOrgCode'), ''
    ) as comp_1_player_1_org,
    case
        when (payload -> 'competitiors' -> 0 -> 'players' -> 0 ->> 'playerPosition') ~ '^[0-9]+$'
            then (payload -> 'competitiors' -> 0 -> 'players' -> 0 ->> 'playerPosition')::integer
    end as comp_1_player_1_position,
    case
        when (payload -> 'competitiors' -> 0 -> 'players' -> 1 ->> 'playerId') ~ '^[0-9]+$'
            then (payload -> 'competitiors' -> 0 -> 'players' -> 1 ->> 'playerId')::bigint
    end as comp_1_player_2_id,
    nullif(
        btrim(payload -> 'competitiors' -> 0 -> 'players' -> 1 ->> 'playerName'), ''
    ) as comp_1_player_2_name,
    nullif(
        btrim(payload -> 'competitiors' -> 0 -> 'players' -> 1 ->> 'playerOrgCode'), ''
    ) as comp_1_player_2_org,
    case
        when (payload -> 'competitiors' -> 0 -> 'players' -> 1 ->> 'playerPosition') ~ '^[0-9]+$'
            then (payload -> 'competitiors' -> 0 -> 'players' -> 1 ->> 'playerPosition')::integer
    end as comp_1_player_2_position,
    nullif(btrim(payload -> 'competitiors' -> 1 ->> 'competitiorId'), '') as comp_2_id,
    nullif(btrim(payload -> 'competitiors' -> 1 ->> 'competitiorName'), '') as comp_2_name,
    nullif(btrim(payload -> 'competitiors' -> 1 ->> 'competitiorOrg'), '') as comp_2_org,
    nullif(btrim(payload -> 'competitiors' -> 1 ->> 'competitorType'), '') as comp_2_type,
    nullif(btrim(payload -> 'competitiors' -> 1 ->> 'scores'), '') as comp_2_scores,
    nullif(btrim(payload -> 'competitiors' -> 1 ->> 'irm'), '') as comp_2_irm,
    case
        when btrim(payload -> 'competitiors' -> 1 ->> 'compTimeout') ~ '^[0-9]+$'
            then btrim(payload -> 'competitiors' -> 1 ->> 'compTimeout')::integer
    end as comp_2_timeout,
    case
        when btrim(payload -> 'competitiors' -> 1 ->> 'compMedicalTimeout') ~ '^[0-9]+$'
            then btrim(payload -> 'competitiors' -> 1 ->> 'compMedicalTimeout')::integer
    end as comp_2_medical_timeout,
    case
        when btrim(payload -> 'competitiors' -> 1 ->> 'compCardY') ~ '^[0-9]+$'
            then btrim(payload -> 'competitiors' -> 1 ->> 'compCardY')::integer
    end as comp_2_card_y,
    case
        when btrim(payload -> 'competitiors' -> 1 ->> 'compCardYR1') ~ '^[0-9]+$'
            then btrim(payload -> 'competitiors' -> 1 ->> 'compCardYR1')::integer
    end as comp_2_card_yr1,
    case
        when btrim(payload -> 'competitiors' -> 1 ->> 'compCardYR2') ~ '^[0-9]+$'
            then btrim(payload -> 'competitiors' -> 1 ->> 'compCardYR2')::integer
    end as comp_2_card_yr2,
    case
        when (payload -> 'competitiors' -> 1 -> 'players' -> 0 ->> 'playerId') ~ '^[0-9]+$'
            then (payload -> 'competitiors' -> 1 -> 'players' -> 0 ->> 'playerId')::bigint
    end as comp_2_player_1_id,
    nullif(
        btrim(payload -> 'competitiors' -> 1 -> 'players' -> 0 ->> 'playerName'), ''
    ) as comp_2_player_1_name,
    nullif(
        btrim(payload -> 'competitiors' -> 1 -> 'players' -> 0 ->> 'playerOrgCode'), ''
    ) as comp_2_player_1_org,
    case
        when (payload -> 'competitiors' -> 1 -> 'players' -> 0 ->> 'playerPosition') ~ '^[0-9]+$'
            then (payload -> 'competitiors' -> 1 -> 'players' -> 0 ->> 'playerPosition')::integer
    end as comp_2_player_1_position,
    case
        when (payload -> 'competitiors' -> 1 -> 'players' -> 1 ->> 'playerId') ~ '^[0-9]+$'
            then (payload -> 'competitiors' -> 1 -> 'players' -> 1 ->> 'playerId')::bigint
    end as comp_2_player_2_id,
    nullif(
        btrim(payload -> 'competitiors' -> 1 -> 'players' -> 1 ->> 'playerName'), ''
    ) as comp_2_player_2_name,
    nullif(
        btrim(payload -> 'competitiors' -> 1 -> 'players' -> 1 ->> 'playerOrgCode'), ''
    ) as comp_2_player_2_org,
    case
        when (payload -> 'competitiors' -> 1 -> 'players' -> 1 ->> 'playerPosition') ~ '^[0-9]+$'
            then (payload -> 'competitiors' -> 1 -> 'players' -> 1 ->> 'playerPosition')::integer
    end as comp_2_player_2_position
from source

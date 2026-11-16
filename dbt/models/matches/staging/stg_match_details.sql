{{ config(materialized='table') }}

with source as (
    select
        event_id,
        document_code,
        payload
    from {{ source('ingest', 'match_details_raw') }}
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
        when (payload -> 'matchConfig' ->> 'bestOf') ~ '^[0-9]+$'
            then (payload -> 'matchConfig' ->> 'bestOf')::integer
    end as best_of,
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
    case
        when (payload -> 'competitiors' -> 0 -> 'players' -> 0 ->> 'playerId') ~ '^[0-9]+$'
            then (payload -> 'competitiors' -> 0 -> 'players' -> 0 ->> 'playerId')::bigint
    end as comp_1_player_1_id,
    case
        when (payload -> 'competitiors' -> 0 -> 'players' -> 1 ->> 'playerId') ~ '^[0-9]+$'
            then (payload -> 'competitiors' -> 0 -> 'players' -> 1 ->> 'playerId')::bigint
    end as comp_1_player_2_id,
    nullif(btrim(payload -> 'competitiors' -> 1 ->> 'competitiorId'), '') as comp_2_id,
    nullif(btrim(payload -> 'competitiors' -> 1 ->> 'competitiorName'), '') as comp_2_name,
    nullif(btrim(payload -> 'competitiors' -> 1 ->> 'competitiorOrg'), '') as comp_2_org,
    nullif(btrim(payload -> 'competitiors' -> 1 ->> 'competitorType'), '') as comp_2_type,
    nullif(btrim(payload -> 'competitiors' -> 1 ->> 'scores'), '') as comp_2_scores,
    case
        when (payload -> 'competitiors' -> 1 -> 'players' -> 0 ->> 'playerId') ~ '^[0-9]+$'
            then (payload -> 'competitiors' -> 1 -> 'players' -> 0 ->> 'playerId')::bigint
    end as comp_2_player_1_id,
    case
        when (payload -> 'competitiors' -> 1 -> 'players' -> 1 ->> 'playerId') ~ '^[0-9]+$'
            then (payload -> 'competitiors' -> 1 -> 'players' -> 1 ->> 'playerId')::bigint
    end as comp_2_player_2_id
from source

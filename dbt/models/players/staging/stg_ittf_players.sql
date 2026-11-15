{{ config(materialized='view') }}

select
    player_id,
    nullif(btrim(hand), '') as hand,
    nullif(btrim(grip), '') as grip,
    nullif(btrim(style), '') as style
from {{ source('ingest', 'ittf_player_profiles') }}

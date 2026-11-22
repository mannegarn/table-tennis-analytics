{{ config(
    alias='dim_player_profiles',
    materialized='view',
) }}

select
    player_id,
    hand,
    case
        when grip in ('Shake hand', 'Handshake') then 'Shakehand'
        else grip
    end as grip,
    style
from {{ ref('stg_ittf_players') }}

{{ config(
    alias='dim_players',
    materialized='table',
) }}

with wtt_normalized as (
    select
        player_id,
        player_name,
        player_given_name,
        player_family_name,
        country_code,
        country_name,
        organization_code,
        organization_name,
        dob,
        gender,
        case
            when handedness ~* '^right' then 'Right'
            when handedness ~* '^left' then 'Left'
        end as normalized_hand,
        case
            when grip ~* '^(shake|handshake|clássico|fl$|st$)' then 'Shakehand'
            when grip ~* '^penhold' then 'Penhold'
        end as normalized_grip,
        head_shot
    from {{ ref('stg_wtt_players') }}
)

select
    {{ player_sk('w.player_id') }} as player_sk,
    w.player_id::text as ittf_id,
    w.player_name,
    w.player_given_name,
    w.player_family_name,
    w.country_code,
    w.country_name,
    w.organization_code,
    w.organization_name,
    w.dob,
    w.gender,
    coalesce(w.normalized_hand, i.hand) as hand,
    coalesce(w.normalized_grip, i.grip) as grip,
    i.style,
    w.head_shot
from wtt_normalized as w
left join {{ ref('ittf__dim_player_profiles') }} as i
    on w.player_id = i.player_id

select
    player_sk,
    ittf_id,
    player_name,
    player_given_name,
    player_family_name,
    country_code,
    country_name,
    organization_code,
    organization_name,
    dob,
    gender,
    hand,
    grip,
    style,
    head_shot,
    has_wtt_profile
from {{ ref('wtt__dim_players') }}

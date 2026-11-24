{{ config(severity='warn') }}

-- Surface team-rubber player IDs absent from wtt__dim_players as a warning
-- rather than a build-blocking error: a future event may reference a player
-- whose WTT GetPlayers response is empty ({"Result": []}) until dim_players
-- stubs unprofiled players.
with rubber_players as (
    select comp_1_player_1_id as player_id
    from {{ ref('stg_team_match_rubbers') }}
    where comp_1_player_1_id is not null
    union
    select comp_1_player_2_id as player_id
    from {{ ref('stg_team_match_rubbers') }}
    where comp_1_player_2_id is not null
    union
    select comp_2_player_1_id as player_id
    from {{ ref('stg_team_match_rubbers') }}
    where comp_2_player_1_id is not null
    union
    select comp_2_player_2_id as player_id
    from {{ ref('stg_team_match_rubbers') }}
    where comp_2_player_2_id is not null
)

select rp.player_id
from rubber_players as rp
left join {{ ref('wtt__dim_players') }} as dp
    on rp.player_id::text = dp.ittf_id
where dp.ittf_id is null

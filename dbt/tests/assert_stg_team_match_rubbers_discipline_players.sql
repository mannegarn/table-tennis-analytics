select
    event_id,
    document_code,
    discipline,
    comp_1_player_2_id,
    comp_2_player_2_id
from {{ ref('stg_team_match_rubbers') }}
where
    (
        discipline = 'singles'
        and (comp_1_player_2_id is not null or comp_2_player_2_id is not null)
    )
    or (
        discipline in ('doubles', 'mixed_doubles')
        and (comp_1_player_2_id is null or comp_2_player_2_id is null)
    )

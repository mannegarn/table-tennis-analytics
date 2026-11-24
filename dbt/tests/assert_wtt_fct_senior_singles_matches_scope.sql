-- Assert fct_senior_singles_matches contains strictly senior able-bodied
-- singles OFFICIAL matches (0 youth, 0 Para, 0 doubles/team, 0 LIVE, 0 BYE,
-- 0 Event 2615 corrupted R4/R5 rubbers, 0 self-play).
select
    fm.match_sk,
    fm.source_event_id,
    fm.document_code
from {{ ref('wtt__fct_senior_singles_matches') }} as fm
inner join {{ ref('wtt__dim_matches') }} as dm
    on fm.match_sk = dm.match_sk
where
    dm.discipline <> 'singles'
    or dm.is_youth
    or dm.is_para
    or dm.is_live
    or fm.p1_player_sk = fm.p2_player_sk
    or fm.p1_player_name ilike 'bye'
    or fm.p2_player_name ilike 'bye'
    or (fm.is_team_rubber and fm.source_event_id = 2615 and fm.rubber_number in (4, 5))

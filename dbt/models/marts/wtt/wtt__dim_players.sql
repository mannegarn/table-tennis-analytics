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
        head_shot,
        true as has_wtt_profile
    from {{ ref('stg_wtt_players') }}
),

match_player_appearances as (
    select
        slot.player_id,
        slot.player_name,
        slot.player_org,
        e.gender_category,
        d.event_id,
        d.document_code
    from {{ ref('stg_match_details') }} as d
    inner join {{ ref('stg_match_entries') }} as e
        on
            d.event_id = e.event_id
            and d.document_code = e.document_code
    cross join
        lateral (
            values
            (d.comp_1_player_1_id, d.comp_1_player_1_name, d.comp_1_player_1_org),
            (d.comp_1_player_2_id, d.comp_1_player_2_name, d.comp_1_player_2_org),
            (d.comp_2_player_1_id, d.comp_2_player_1_name, d.comp_2_player_1_org),
            (d.comp_2_player_2_id, d.comp_2_player_2_name, d.comp_2_player_2_org),
            (
                case
                    when
                        e.discipline = 'singles'
                        and d.comp_1_player_1_id is null
                        and d.comp_1_id ~ '^[0-9]+$'
                        and d.comp_1_id::bigint > 0
                        then d.comp_1_id::bigint
                end,
                d.comp_1_name,
                d.comp_1_org
            ),
            (
                case
                    when
                        e.discipline = 'singles'
                        and d.comp_2_player_1_id is null
                        and d.comp_2_id ~ '^[0-9]+$'
                        and d.comp_2_id::bigint > 0
                        then d.comp_2_id::bigint
                end,
                d.comp_2_name,
                d.comp_2_org
            )
        ) as slot (player_id, player_name, player_org)
    where
        e.discipline <> 'team'
        and slot.player_id is not null

    union all

    select
        slot.player_id,
        slot.player_name,
        slot.player_org,
        r.gender_category,
        r.event_id,
        r.document_code
    from {{ ref('stg_team_match_rubbers') }} as r
    cross join
        lateral (
            values
            (r.comp_1_player_1_id, r.comp_1_player_1_name, r.comp_1_player_1_org),
            (r.comp_1_player_2_id, r.comp_1_player_2_name, r.comp_1_player_2_org),
            (r.comp_2_player_1_id, r.comp_2_player_1_name, r.comp_2_player_1_org),
            (r.comp_2_player_2_id, r.comp_2_player_2_name, r.comp_2_player_2_org)
        ) as slot (player_id, player_name, player_org)
    where slot.player_id is not null
),

unprofiled_stubs as (
    select distinct on (a.player_id)
        a.player_id,
        coalesce(a.player_name, 'Unknown Player ' || a.player_id::text) as player_name,
        null::text as player_given_name,
        null::text as player_family_name,
        a.player_org as country_code,
        null::text as country_name,
        a.player_org as organization_code,
        null::text as organization_name,
        null::date as dob,
        case
            when a.gender_category = 'men' then 'M'
            when a.gender_category = 'women' then 'F'
        end as gender,
        null::text as normalized_hand,
        null::text as normalized_grip,
        null::text as head_shot,
        false as has_wtt_profile
    from match_player_appearances as a
    left join wtt_normalized as w
        on a.player_id = w.player_id
    where w.player_id is null
    order by
        a.player_id asc,
        (a.player_name is null) asc,
        (a.player_org is null) asc,
        case when a.gender_category in ('men', 'women') then 0 else 1 end asc,
        a.event_id desc,
        a.document_code desc
),

all_players as (
    select * from wtt_normalized
    union all
    select * from unprofiled_stubs
)

select
    {{ player_sk('p.player_id') }} as player_sk,
    p.player_id::text as ittf_id,
    p.player_name,
    p.player_given_name,
    p.player_family_name,
    p.country_code,
    p.country_name,
    p.organization_code,
    p.organization_name,
    p.dob,
    p.gender,
    coalesce(nullif(btrim(o.hand), ''), p.normalized_hand, i.hand) as hand,
    coalesce(nullif(btrim(o.grip), ''), p.normalized_grip, i.grip) as grip,
    coalesce(nullif(btrim(o.style), ''), i.style) as style,
    p.head_shot,
    p.has_wtt_profile
from all_players as p
left join {{ ref('ittf__dim_player_profiles') }} as i
    on p.player_id = i.player_id
left join {{ ref('player_profile_overrides') }} as o
    on p.player_id::text = o.ittf_id

{{ config(
    alias='dim_events',
    materialized='table',
) }}

with events_deduped as (
    select distinct on (event_id)
        event_id,
        nullif(btrim(regexp_replace(event_name, '\s+', ' ', 'g')), '') as event_name,
        event_type,
        event_tier_name,
        start_date,
        end_date,
        nullif(btrim(regexp_replace(country, '\s+', ' ', 'g')), '') as country,
        nullif(btrim(regexp_replace(city, '\s+', ' ', 'g')), '') as city,
        continent_code
    from {{ ref('stg_events') }}
    order by
        event_id asc,
        source_year desc
),

events_with_matches as (
    select distinct event_id
    from {{ ref('stg_match_entries') }}
),

events_with_match_details as (
    select distinct event_id
    from {{ ref('stg_match_details') }}
),

events_with_commentary as (
    select distinct event_id
    from {{ ref('stg_match_commentary') }}
)

select
    {{ surrogate_key("'wtt'", ['e.event_id']) }} as event_sk,
    'wtt'::text as source_system,
    e.event_id as source_event_id,
    e.event_name,
    e.event_type,
    e.event_tier_name,
    extract(year from e.start_date)::smallint as event_year,
    e.start_date,
    e.end_date,
    case
        when e.start_date <= e.end_date then (e.end_date - e.start_date + 1)
    end as duration_days,
    e.country,
    e.city,
    e.continent_code,
    s.ball_sponsor,
    s.table_sponsor,
    (m.event_id is not null) as has_matches,
    (md.event_id is not null) as has_match_details,
    (mc.event_id is not null) as has_commentary,
    (s.ball_sponsor is not null or s.table_sponsor is not null) as has_sponsors
from events_deduped as e
left join {{ ref('int_event_equipment_sponsors') }} as s
    on e.event_id = s.event_id
left join events_with_matches as m
    on e.event_id = m.event_id
left join events_with_match_details as md
    on e.event_id = md.event_id
left join events_with_commentary as mc
    on e.event_id = mc.event_id

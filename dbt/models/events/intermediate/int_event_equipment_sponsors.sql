{{ config(materialized='table') }}

with link_extracted as (
    select
        s.event_id,
        s.sponsor_type_name,
        d.sponsor_name
    from {{ ref('stg_event_sponsors') }} as s
    inner join {{ ref('sponsor_domains') }} as d
        on lower(
            split_part(
                split_part(
                    regexp_replace(
                        regexp_replace(btrim(s.sponsor_link), '^https?://', '', 'i'),
                        '^www\.',
                        '',
                        'i'
                    ),
                    '?',
                    1
                ),
                '/',
                1
            )
        ) = d.domain
    where
        s.sponsor_link is not null
        and s.sponsor_type_name in (
            'Official Ball',
            'Official Ball Partner',
            'Official Table',
            'Official Table Partner',
            'Official Table & Ball',
            'Official Table, Ball & Floor',
            'Official Table & Floor'
        )
),

link_by_event as (
    select
        event_id,
        max(
            case
                when
                    sponsor_type_name in (
                        'Official Ball',
                        'Official Ball Partner',
                        'Official Table & Ball',
                        'Official Table, Ball & Floor'
                    )
                    then sponsor_name
            end
        ) as ball_sponsor,
        max(
            case
                when
                    sponsor_type_name in (
                        'Official Table',
                        'Official Table Partner',
                        'Official Table & Ball',
                        'Official Table, Ball & Floor',
                        'Official Table & Floor'
                    )
                    then sponsor_name
            end
        ) as table_sponsor
    from link_extracted
    group by event_id
),

manual as (
    select
        event_id,
        nullif(btrim(ball_sponsor), '') as ball_sponsor,
        nullif(btrim(table_sponsor), '') as table_sponsor
    from {{ ref('event_sponsors_manual') }}
)

select
    coalesce(m.event_id, l.event_id) as event_id,
    coalesce(m.ball_sponsor, l.ball_sponsor) as ball_sponsor,
    coalesce(m.table_sponsor, l.table_sponsor) as table_sponsor,
    case
        when m.ball_sponsor is not null then 'manual_csv'
        when l.ball_sponsor is not null then 'sponsor_link'
    end as ball_sponsor_source,
    case
        when m.table_sponsor is not null then 'manual_csv'
        when l.table_sponsor is not null then 'sponsor_link'
    end as table_sponsor_source
from manual as m
full outer join link_by_event as l
    on m.event_id = l.event_id

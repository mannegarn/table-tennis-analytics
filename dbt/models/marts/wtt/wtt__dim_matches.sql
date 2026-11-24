{{ config(
    alias='dim_matches',
    materialized='table',
    post_hook=[
        "create unique index on {{ this }} (match_sk)",
        "create index on {{ this }} (event_sk)",
        "create unique index on {{ this }} (source_system, source_event_id, document_code)",
        "analyze {{ this }}",
    ],
) }}

with entry_matches as (
    select
        e.event_id,
        e.document_code,
        false as is_team_rubber,
        null::text as parent_document_code,
        null::smallint as rubber_number,
        e.match_id as source_match_id,
        e.discipline,
        e.gender_category,
        e.sub_event_type,
        d.sub_event_name,
        d.sub_event_description,
        e.age_category,
        e.is_youth,
        e.is_para,
        e.para_class,
        case
            when e.discipline = 'team' and d.sub_event_description ilike 'stage 2 - group%'
                then 'GP##'
            when
                e.discipline = 'team'
                and e.raw_round_token = 'FNL-'
                and d.sub_event_description ilike '%bronze%'
                then 'FNL2'
            else e.raw_round_token
        end as raw_round_token,
        e.group_number,
        e.match_number_in_round,
        d.start_date_utc,
        coalesce(d.start_date_local, e.start_date_local) as start_date_local,
        d.venue_name,
        d.table_name,
        d.table_number,
        d.duration,
        d.ttr_review,
        d.max_challenges_per_competitor,
        coalesce(d.result_status, e.full_results) as result_status,
        (d.event_id is not null) as has_match_details
    from {{ ref('stg_match_entries') }} as e
    left join {{ ref('stg_match_details') }} as d
        on
            e.event_id = d.event_id
            and e.document_code = d.document_code
),

team_rubbers as (
    select
        r.event_id,
        r.document_code,
        true as is_team_rubber,
        r.parent_document_code,
        r.rubber_number,
        null::bigint as source_match_id,
        r.discipline,
        r.gender_category,
        pe.sub_event_type,
        r.sub_event_name,
        r.sub_event_description,
        r.age_category,
        r.is_youth,
        r.is_para,
        r.para_class,
        r.raw_round_token,
        r.group_number,
        r.match_number_in_round,
        r.start_date_utc,
        r.start_date_local,
        r.venue_name,
        r.table_name,
        r.table_number,
        r.duration,
        r.ttr_review,
        r.max_challenges_per_competitor,
        r.result_status,
        true as has_match_details
    from {{ ref('stg_team_match_rubbers') }} as r
    left join {{ ref('stg_match_entries') }} as pe
        on
            r.event_id = pe.event_id
            and r.parent_document_code = pe.document_code
),

all_matches as (
    select * from entry_matches
    union all
    select * from team_rubbers
),

commentary_matches as (
    select distinct
        event_id,
        document_code
    from {{ ref('stg_match_commentary') }}
)

select
    {{ surrogate_key("'wtt'", ['m.event_id', 'm.document_code']) }} as match_sk,
    ev.event_sk,
    'wtt'::text as source_system,
    m.event_id as source_event_id,
    m.document_code,
    m.parent_document_code,
    m.rubber_number,
    m.source_match_id,
    m.discipline,
    m.is_team_rubber,
    m.gender_category,
    m.sub_event_type,
    m.sub_event_name,
    m.sub_event_description,
    m.age_category,
    m.is_youth,
    (
        m.is_para
        or ev.event_type = 'Para Open'
        or ev.event_name ~* '\m(para|paralympic)\M'
    ) as is_para,
    m.para_class,
    m.raw_round_token,
    rc.round_code,
    rc.round_name,
    rc.round_stage,
    rc.round_order,
    m.group_number,
    m.match_number_in_round,
    m.start_date_utc,
    m.start_date_local,
    m.venue_name,
    m.table_name,
    m.table_number,
    m.duration,
    m.ttr_review,
    m.max_challenges_per_competitor,
    m.result_status,
    coalesce(m.result_status = 'LIVE', false) as is_live,
    m.has_match_details,
    (cm.event_id is not null) as has_commentary
from all_matches as m
inner join {{ ref('wtt__dim_events') }} as ev
    on m.event_id = ev.source_event_id
inner join {{ ref('round_codes') }} as rc
    on m.raw_round_token = rc.raw_round_token
left join commentary_matches as cm
    on
        m.event_id = cm.event_id
        and m.document_code = cm.document_code

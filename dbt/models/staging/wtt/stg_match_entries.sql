{{ config(
    materialized='table',
    post_hook="analyze {{ this }}",
) }}

with deduped as (
    select distinct on (
        r.event_id,
        nullif(btrim(elem ->> 'documentCode'), '')
    )
        r.event_id,
        nullif(btrim(elem ->> 'documentCode'), '') as document_code,
        coalesce((elem ->> 'id')::bigint, (elem ->> 'iD')::bigint) as match_id,
        nullif(btrim(elem ->> 'subEventType'), '') as sub_event_type,
        nullif(btrim(elem ->> 'fullResults'), '') as full_results,
        case
            when nullif(btrim(elem ->> 'startDateLocal'), '') like '0001-01-01%' then null
            else nullif(btrim(elem ->> 'startDateLocal'), '')::timestamp
        end as start_date_local
    from {{ source('wtt', 'match_entries_payloads_raw') }} as r
    cross join lateral jsonb_array_elements(r.payload) as elem
    order by
        r.event_id,
        nullif(btrim(elem ->> 'documentCode'), ''),
        coalesce((elem ->> 'id')::bigint, (elem ->> 'iD')::bigint)
),

parsed as (
    select
        event_id,
        document_code,
        match_id,
        sub_event_type,
        case
            when sub_event_type like '%Singles' then 'singles'
            when sub_event_type like '%Mixed Doubles' then 'mixed_doubles'
            when sub_event_type like '%Doubles' then 'doubles'
            when sub_event_type like '%Team' then 'team'
        end as discipline,
        case substring(document_code from 4 for 1)
            when 'M' then 'men'
            when 'W' then 'women'
            when 'X' then 'mixed'
        end as gender_category,
        coalesce(
            'U' || (
                regexp_match(
                    document_code,
                    '^TTE[MWX](?:SINGLES|DOUBLES|TEAM)-+U([0-9]{2})'
                )
            )[1],
            'senior'
        ) as age_category,
        case
            when substring(document_code from 16 for 4) ~ '^[MWX][SDT][0-9]+'
                then btrim(substring(document_code from 16 for 4), '-')
        end as para_class,
        substring(document_code from 23 for 4) as round_token_pos23,
        substring(document_code from 27 for 4) as match_num_pos27,
        full_results,
        start_date_local
    from deduped
)

select
    event_id,
    document_code,
    match_id,
    sub_event_type,
    discipline,
    gender_category,
    age_category,
    age_category <> 'senior' as is_youth,
    para_class,
    para_class is not null as is_para,
    case
        when round_token_pos23 ~ '^GP[0-9]{2}$' then 'GP##'
        when
            round_token_pos23 = 'FNL-'
            and match_num_pos27 = '0002'
            and discipline <> 'team' then 'FNL2'
        else round_token_pos23
    end as raw_round_token,
    case
        when round_token_pos23 ~ '^GP[0-9]{2}$'
            then substring(round_token_pos23 from 3 for 2)::smallint
    end as group_number,
    case
        when match_num_pos27 ~ '^[0-9]{4}$'
            then match_num_pos27::integer
    end as match_number_in_round,
    full_results,
    start_date_local
from parsed

{{ config(severity='warn') }}

-- Surface LIVE (unfinished upstream) matches in dim_matches for visibility
-- without blocking dbt-run.
select
    match_sk,
    source_event_id,
    document_code,
    discipline,
    result_status
from {{ ref('wtt__dim_matches') }}
where is_live

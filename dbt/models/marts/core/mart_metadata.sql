{{ config(materialized='table') }}

select current_timestamp as built_at_utc

{{ config(materialized='view') }}

-- Note: nationality_code/nationality_name and headshot_l/headshot_r are currently
-- redundant with country_code/country_name and head_shot, so they are omitted here
-- and can be re-exposed if future ingests diverge.
select
    player_id,
    nullif(btrim(player_name), '') as player_name,
    nullif(btrim(player_given_name), '') as player_given_name,
    nullif(btrim(player_family_name), '') as player_family_name,
    nullif(btrim(country_code), '') as country_code,
    nullif(btrim(country_name), '') as country_name,
    nullif(btrim(organization_code), '') as organization_code,
    nullif(btrim(organization_name), '') as organization_name,
    nullif(btrim(gender), '') as gender,
    age,
    to_date(nullif(btrim(dob), ''), 'MM/DD/YYYY 00:00:00') as dob,
    nullif(btrim(handedness), '') as handedness,
    nullif(btrim(grip), '') as grip,
    nullif(btrim(head_shot), '') as head_shot
from {{ source('ingest', 'player_details_unpacked') }}

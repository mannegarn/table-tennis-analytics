{{ config(materialized='table') }}

-- Note: nationality_code/nationality_name and headshot_l/headshot_r are currently
-- redundant with country_code/country_name and head_shot, so they are omitted here
-- and can be re-exposed if future ingests diverge.
select distinct on (r.player_id)
    r.player_id,
    nullif(btrim(e.value ->> 'PlayerName'), '') as player_name,
    nullif(btrim(e.value ->> 'PlayerGivenName'), '') as player_given_name,
    nullif(btrim(e.value ->> 'PlayerFamilyName'), '') as player_family_name,
    nullif(btrim(e.value ->> 'CountryCode'), '') as country_code,
    nullif(btrim(e.value ->> 'CountryName'), '') as country_name,
    nullif(btrim(e.value ->> 'OrganizationCode'), '') as organization_code,
    nullif(btrim(e.value ->> 'OrganizationName'), '') as organization_name,
    nullif(btrim(e.value ->> 'Gender'), '') as gender,
    case
        when (e.value ->> 'Age') ~ '^[0-9]+$'
            then (e.value ->> 'Age')::integer
    end as age,
    to_date(nullif(btrim(e.value ->> 'DOB'), ''), 'MM/DD/YYYY 00:00:00') as dob,
    nullif(btrim(e.value ->> 'Handedness'), '') as handedness,
    nullif(btrim(e.value ->> 'Grip'), '') as grip,
    nullif(btrim(e.value ->> 'HeadShot'), '') as head_shot
from {{ source('wtt', 'player_details_raw') }} as r
cross join lateral jsonb_array_elements(coalesce(r.payload -> 'Result', '[]'::jsonb)) as e
where jsonb_typeof(r.payload -> 'Result') = 'array'
order by r.player_id

{{ config(severity='warn') }}

-- Never auto-merge players on name alone; surface exact (player_name, country_code)
-- collisions so they stay visible for manual review.
select
    player_name,
    country_code,
    count(*) as row_count
from {{ ref('wtt__dim_players') }}
group by 1, 2
having count(*) > 1

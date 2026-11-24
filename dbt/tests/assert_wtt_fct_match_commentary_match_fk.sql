-- Fast distinct-key FK check from 6.77M-row fct_match_commentary to dim_matches.
with commentary_matches as (
    select distinct match_sk
    from {{ ref('wtt__fct_match_commentary') }}
)

select cm.match_sk
from commentary_matches as cm
left join {{ ref('wtt__dim_matches') }} as dm
    on cm.match_sk = dm.match_sk
where dm.match_sk is null

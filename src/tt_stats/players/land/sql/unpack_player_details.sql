-- =============================================================================
-- unpack_player_details.sql
--
-- Flattens one player's Result payload from ingest.player_details_raw into
-- one row per player_id in ingest.player_details_unpacked.
-- =============================================================================

INSERT INTO ingest.player_details_unpacked (
    player_id,
    player_name,
    player_given_name,
    player_family_name,
    player_family_name_first,
    country_code,
    country_name,
    nationality_code,
    nationality_name,
    organization_code,
    organization_name,
    gender,
    age,
    dob,
    handedness,
    grip,
    style,
    equipment_sponsor,
    blade_type,
    racket_coloring_a,
    racket_coloring_b,
    racket_covering_a,
    racket_covering_b,
    active_since,
    earnings_career,
    earnings_ytd,
    last_match,
    next_match,
    head_shot,
    bio,
    headshot_r,
    headshot_l,
    first_seen_at,
    last_seen_at
)
SELECT DISTINCT ON (r.player_id)
       r.player_id                                            AS player_id,
       (e.value ->> 'PlayerName')::text                       AS player_name,
       (e.value ->> 'PlayerGivenName')::text                  AS player_given_name,
       (e.value ->> 'PlayerFamilyName')::text                 AS player_family_name,
       (e.value ->> 'PlayerFamilyNameFirst')::text            AS player_family_name_first,
       (e.value ->> 'CountryCode')::text                      AS country_code,
       (e.value ->> 'CountryName')::text                      AS country_name,
       (e.value ->> 'NationalityCode')::text                  AS nationality_code,
       (e.value ->> 'NationalityName')::text                  AS nationality_name,
       (e.value ->> 'OrganizationCode')::text                 AS organization_code,
       (e.value ->> 'OrganizationName')::text                 AS organization_name,
       (e.value ->> 'Gender')::text                           AS gender,
       CASE
           WHEN (e.value ->> 'Age') ~ '^[0-9]+$'
               THEN (e.value ->> 'Age')::integer
       END                                                    AS age,
       (e.value ->> 'DOB')::text                              AS dob,
       (e.value ->> 'Handedness')::text                       AS handedness,
       (e.value ->> 'Grip')::text                             AS grip,
       (e.value ->> 'Style')::text                            AS style,
       (e.value ->> 'EquipmentSponsor')::text                 AS equipment_sponsor,
       (e.value ->> 'BladeType')::text                        AS blade_type,
       (e.value ->> 'RacketColoringA')::text                  AS racket_coloring_a,
       (e.value ->> 'RacketColoringB')::text                  AS racket_coloring_b,
       (e.value ->> 'RacketCoveringA')::text                  AS racket_covering_a,
       (e.value ->> 'RacketCoveringB')::text                  AS racket_covering_b,
       (e.value ->> 'ActiveSince')::text                      AS active_since,
       (e.value ->> 'EarningsCareer')::text                   AS earnings_career,
       (e.value ->> 'EarningsYTD')::text                      AS earnings_ytd,
       (e.value ->> 'LastMatch')::text                        AS last_match,
       (e.value ->> 'NextMatch')::text                        AS next_match,
       (e.value ->> 'HeadShot')::text                         AS head_shot,
       (e.value ->> 'Bio')::text                              AS bio,
       (e.value ->> 'HeadshotR')::text                        AS headshot_r,
       (e.value ->> 'HeadshotL')::text                        AS headshot_l,
       clock_timestamp()                                      AS first_seen_at,
       clock_timestamp()                                      AS last_seen_at
FROM ingest.player_details_raw AS r
CROSS JOIN LATERAL jsonb_array_elements(
    COALESCE(r.payload -> 'Result', '[]'::jsonb)
) AS e
WHERE r.player_id = %(player_id)s
  AND jsonb_typeof(r.payload -> 'Result') = 'array'
ORDER BY r.player_id
ON CONFLICT (player_id) DO UPDATE SET
    player_name              = EXCLUDED.player_name,
    player_given_name        = EXCLUDED.player_given_name,
    player_family_name       = EXCLUDED.player_family_name,
    player_family_name_first = EXCLUDED.player_family_name_first,
    country_code             = EXCLUDED.country_code,
    country_name             = EXCLUDED.country_name,
    nationality_code         = EXCLUDED.nationality_code,
    nationality_name         = EXCLUDED.nationality_name,
    organization_code        = EXCLUDED.organization_code,
    organization_name        = EXCLUDED.organization_name,
    gender                   = EXCLUDED.gender,
    age                      = EXCLUDED.age,
    dob                      = EXCLUDED.dob,
    handedness               = EXCLUDED.handedness,
    grip                     = EXCLUDED.grip,
    style                    = EXCLUDED.style,
    equipment_sponsor        = EXCLUDED.equipment_sponsor,
    blade_type               = EXCLUDED.blade_type,
    racket_coloring_a        = EXCLUDED.racket_coloring_a,
    racket_coloring_b        = EXCLUDED.racket_coloring_b,
    racket_covering_a        = EXCLUDED.racket_covering_a,
    racket_covering_b        = EXCLUDED.racket_covering_b,
    active_since             = EXCLUDED.active_since,
    earnings_career          = EXCLUDED.earnings_career,
    earnings_ytd             = EXCLUDED.earnings_ytd,
    last_match               = EXCLUDED.last_match,
    next_match               = EXCLUDED.next_match,
    head_shot                = EXCLUDED.head_shot,
    bio                      = EXCLUDED.bio,
    headshot_r               = EXCLUDED.headshot_r,
    headshot_l               = EXCLUDED.headshot_l,
    last_seen_at             = EXCLUDED.last_seen_at;

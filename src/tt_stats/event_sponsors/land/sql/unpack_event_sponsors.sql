-- =============================================================================
-- unpack_event_sponsors.sql
--
-- Turns one event's WTT sponsor/equipment payload from JSON into one row per
-- sponsor. Structure only: no filtering, no business rules (ADR 0003).
--
-- WHEN IT RUNS
--   1. Automatically, on every collect: after the raw row is written, in the
--      same transaction. If this fails, the raw write rolls back too.
--   2. By hand, via `make event-sponsors-unpack`, to backfill payloads that
--      landed before this unpack existed.
-- =============================================================================

INSERT INTO ingest.event_sponsors_unpacked (
    event_id,
    event_sponsor_id,
    sponsor_type_id,
    sponsor_type_name,
    logo,
    dark_logo,
    sponsor_link,
    first_seen_at,
    last_seen_at
)
SELECT DISTINCT ON (r.event_id, (e.value ->> 'eventSponsorId')::bigint)
       r.event_id                                       AS event_id,
       (e.value ->> 'eventSponsorId')::bigint           AS event_sponsor_id,
       (e.value ->> 'sponsorTypeId')::integer           AS sponsor_type_id,
       (e.value ->> 'sponsorTypeName')::text            AS sponsor_type_name,
       (e.value ->> 'logo')::text                       AS logo,
       (e.value ->> 'darkLogo')::text                   AS dark_logo,
       (e.value ->> 'sponsorLink')::text                AS sponsor_link,
       clock_timestamp()                                AS first_seen_at,
       clock_timestamp()                                AS last_seen_at
FROM ingest.event_sponsors_raw AS r
CROSS JOIN LATERAL jsonb_array_elements(r.payload) AS e
WHERE r.event_id = %(event_id)s
ORDER BY
    r.event_id,
    (e.value ->> 'eventSponsorId')::bigint
ON CONFLICT (event_id, event_sponsor_id) DO UPDATE SET
    sponsor_type_id   = EXCLUDED.sponsor_type_id,
    sponsor_type_name = EXCLUDED.sponsor_type_name,
    logo              = EXCLUDED.logo,
    dark_logo         = EXCLUDED.dark_logo,
    sponsor_link      = EXCLUDED.sponsor_link,
    last_seen_at      = EXCLUDED.last_seen_at;

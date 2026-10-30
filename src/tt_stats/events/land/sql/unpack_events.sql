-- =============================================================================
-- unpack_events.sql
--
-- Turns one year of the WTT event calendar from JSON into normal rows.
--
-- WHAT "UNPACKING" MEANS HERE
--   The raw table holds the API response exactly as it arrived, which is one
--   big JSON blob per year. You cannot filter, join or index inside a blob
--   easily, so we copy the useful fields out into real columns. That copy is
--   this file. The JSON stays put, untouched, so if we ever get the mapping
--   wrong we can re-run this and the raw data is still there to rebuild from.
--
-- WHEN IT RUNS
--   1. Automatically, on every scrape: after the raw row is written, in the
--      same transaction. If this fails, the raw write is rolled back too, so a
--      year is never half-landed.
--   2. By hand, via `make events-unpack`, to catch up years that landed before
--      this file existed.
--
-- WHAT IT IS NOT
--   Not a filter. It does not care about tier, country, type or whether an
--   event looks interesting. It copies structure only; choosing which events
--   matter is a dbt job later. There is also no DELETE or TRUNCATE anywhere:
--   running it twice must be harmless.
--
-- WHY THE KEY IS (source_year, event_id)
--   A tournament that straddles New Year is listed in both years' payloads, so
--   event_id alone would let the later landing overwrite the earlier row and
--   quietly rewrite its source_year. Keying on the pair keeps both sightings.
--   Within a single year an event_id is still unique, which is what the
--   DISTINCT ON below enforces.
--
-- FRESHNESS
--   first_seen_at is written once, on the first insert, and never touched
--   again. last_seen_at is rewritten on every unpack, so "still in the calendar"
--   means "last_seen_at moved". An event WTT dropped simply stops advancing,
--   and its row stays put so nothing already pointing at it is orphaned.
--
-- NUMBERS TO EXPECT (measured, 29 Oct 2026)
--   2023 -> 101 from 101 landed   (no duplicates that year)
--   2024 -> 131 from 131, 2025 -> 156 from 156
--   2021 ->  86 from  99, 2022 ->  91 from  94, 2026 -> 171 from 174
--           those three years list some events word-for-word twice or more
--   All six years -> 736 distinct event ids from 755 landed calendar rows.
--   All 19 duplicates (2021, 2022, 2026) are repeats inside one year, so no
--   event appears in two years in this data. The key is still the pair
--   (source_year, event_id): that is what protects a New Year straddling
--   tournament, not something these six years currently exercise.
-- =============================================================================


INSERT INTO ingest.events_unpacked (
    source_year,
    event_id,
    event_name,
    start_date,
    end_date,
    first_seen_at,
    last_seen_at
)
-- Naming the columns, rather than relying on their order, means adding a
-- column to the table later cannot quietly shift values into the wrong field.

SELECT DISTINCT ON ((e.value ->> 'EventId')::bigint)
-- DISTINCT ON = "keep only the first row for each of these values".
-- Think of it as de-duplicating a spreadsheet by one column.
-- It is needed because some years (2021, 2022, 2026) list the same event more
-- than once. Without it we would try to insert that event_id twice in one
-- statement, and Postgres refuses that with "ON CONFLICT DO UPDATE cannot
-- affect row a second time".
--
-- Only event_id is used, not the full key: this statement reads one year's
-- payload, so source_year is constant here and adds nothing.

       r.year::smallint                        AS source_year,
-- Which calendar year this row came from. It is half of the key, because the
-- same tournament can legitimately be listed by two years.

       (e.value ->> 'EventId')::bigint         AS event_id,
-- '->>' pulls a field out of the JSON as text. Cast to bigint because the API
-- sends a number and we want arithmetic and joins to work on it.

       (e.value ->> 'EventName')::text         AS event_name,

       (e.value ->> 'StartDateTime')::date     AS start_date,
-- Cast straight to date. The API sends "2023-03-07T00:00:00" with no timezone,
-- so turning it into a timestamp would mean inventing one. A date is honest.

       (e.value ->> 'EndDateTime')::date       AS end_date,

       clock_timestamp()                       AS first_seen_at,
-- Only meaningful on the first insert: the upsert below deliberately leaves it
-- alone, so it keeps recording when this row first appeared.

       clock_timestamp()                       AS last_seen_at
-- Rewritten on every unpack; this is the column that moves while an event is
-- still scheduled.

FROM ingest.events_by_year_raw AS r
-- The raw row for the year we were asked for. The year arrives as a query
-- parameter, filled in by Python, so this same file works for 2021, 2022 and
-- every year after. Keep exactly one placeholder in the whole file: psycopg
-- counts them literally, even inside a comment.

CROSS JOIN LATERAL jsonb_array_elements(r.payload -> 0 -> 'rows') AS e
-- This is the actual unpacking step.
-- The payload looks like: [ { "rows": [ {...}, {...} ] } ]
--   payload -> 0        = the first (only) object in that outer list
--   -> 'rows'           = the array of events inside it
-- jsonb_array_elements turns that array into one row per event.
-- LATERAL lets each of those rows refer back to r from the line above; without
-- it, the function could not see the current raw row.

WHERE r.year = %(year)s
-- Only for the year we asked about. A year that never landed simply returns no
-- rows, which is why the backfill can loop over years safely. The parameter is
-- named in Python as {"year": year}; keep exactly one placeholder in the whole
-- file, because psycopg counts them literally, even inside a comment.

ORDER BY
    (e.value ->> 'EventId')::bigint,
    (e.value ->> 'StartDateTime')::text DESC NULLS LAST
-- DISTINCT ON requires ORDER BY to start with the same expression, which is
-- what the first line does. Written out rather than as "1" because the second
-- line is required too, and mixing a positional reference with a real
-- expression reads as a mistake.
--
-- The tie-breaker makes the choice deterministic: when a year lists an event
-- twice with different start dates, keep the latest-dated copy. Without a
-- tie-breaker Postgres is free to return either row and the values could flip
-- between runs. DESC NULLS LAST puts a blank start date after every real one,
-- so an empty duplicate never wins.

ON CONFLICT (source_year, event_id) DO UPDATE SET
-- What happens when this event was already unpacked for this year: update it
-- instead of failing.
    event_name   = EXCLUDED.event_name,
    start_date   = EXCLUDED.start_date,
    end_date     = EXCLUDED.end_date,
    last_seen_at = EXCLUDED.last_seen_at;
-- EXCLUDED means "the row we just tried to insert". So this reads as "take the
-- newer values".
--
-- Deliberately absent: source_year and event_id (they are the key), and
-- first_seen_at. Leaving first_seen_at out is the whole point of the column;
-- rewriting it here would turn it into a second last_seen_at.

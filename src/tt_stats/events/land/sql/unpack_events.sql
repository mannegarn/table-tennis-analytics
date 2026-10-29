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
-- NUMBERS TO EXPECT (measured, 29 Oct 2026)
--   2023 -> 101 from 101 landed   (no duplicates that year)
--   2024 -> 131 from 131, 2025 -> 156 from 156
--   2021 ->  86 from  99, 2022 ->  91 from  94, 2026 -> 171 from 174
--           those three years list some events word-for-word twice or more
--   All six years -> 736 distinct events out of 755 landed rows
--   The 19 rows lost are duplicates inside a single year, never across years.
-- =============================================================================


INSERT INTO ingest.events_unpacked (
    event_id,
    event_name,
    start_date,
    end_date,
    source_year,
    refreshed_at
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
-- The expression is the event id, which becomes the primary key below.

       (e.value ->> 'EventId')::bigint     AS event_id,
-- '->>' pulls a field out of the JSON as text. Cast to bigint because the API
-- sends a number and we want arithmetic and joins to work on it.

       (e.value ->> 'EventName')::text     AS event_name,

       (e.value ->> 'StartDateTime')::date AS start_date,
-- Cast straight to date. The API sends "2023-03-07T00:00:00" with no timezone,
-- so turning it into a timestamp would mean inventing one. A date is honest.

       (e.value ->> 'EndDateTime')::date   AS end_date,

       r.year::smallint                    AS source_year,
-- Which calendar year this row came from. It is a fact about the fetch, not
-- part of the event's identity, so a re-fetch simply rewrites it.

       now()                               AS refreshed_at
-- "When this unpack ran". The pending view uses this to tell fresh data from
-- stale data, so it must be set every time.

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

WHERE r.year = %s
-- Only for the year we asked about. A year that never landed simply returns no
-- rows, which is why the backfill can loop over years safely.

ORDER BY 1
-- Required by DISTINCT ON: it must sort by the same thing it de-duplicates by.
-- "1" means "the first column in the SELECT list", which is the event id.
-- Writing it as ORDER BY 1 keeps the two expressions guaranteed identical; a
-- typo in a repeated expression would be easy to miss.

ON CONFLICT (event_id) DO UPDATE SET
-- What happens when the event is already there: update it instead of failing.
    event_name   = EXCLUDED.event_name,
    start_date   = EXCLUDED.start_date,
    end_date     = EXCLUDED.end_date,
    source_year  = EXCLUDED.source_year,
    refreshed_at = now();
-- EXCLUDED means "the row we just tried to insert". So this reads as "take the
-- newer values". refreshed_at deliberately uses now() rather than the new
-- value, because it should record when the unpack happened.
--
-- Do not update event_id here: it is the key. Do not forget refreshed_at, or a
-- refresh will still look stale and the queue will keep asking for it.

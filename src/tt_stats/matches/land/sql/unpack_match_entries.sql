-- =============================================================================
-- unpack_match_entries.sql
--
-- Turns one event's WTT match list from JSON into one row per match.
--
-- WHAT "UNPACKING" MEANS HERE
--   The raw table holds the response exactly as it arrived: a bare JSON array
--   per event. You cannot filter, join or index inside an array, so the fields
--   that identify a match are copied into real columns. That copy is this file.
--   The array stays put, so if the mapping is ever wrong the columns can be
--   rebuilt from it.
--
-- WHEN IT RUNS
--   1. Automatically, on every collect: after the raw row is written, in the
--      same transaction (ADR 0003). If this fails, the raw write rolls back too,
--      so an event is never half-landed.
--   2. By hand, via `make matches-unpack`, to catch up the events that landed
--      before this file existed.
--
-- THE KEY IS (event_id, document_code), NOT match id
--   Measured on 200,879 landed rows: a match id repeats inside one event for
--   different matches (278 groups, e.g. event 2411 id 59820 arrives as both
--   QFNL000100 and QFNL000200), and repeats across events (132 ids). The
--   document code is unique within an event, and was never repeated in any
--   payload, so that pair is what a match is (ADR 0010).
--
--   The id is deliberately NOT unpacked: it is neither unique nor global, and
--   nothing reads it, because match details and logs are fetched by
--   (eventId, documentCode) (ADR 0009). It stays in the payload.
--
-- WHAT STAYS IN THE PAYLOAD
--   id / iD, startDateLocal, subEventType and fullResults are all left as JSON.
--   None of them is needed to identify a match, to schedule the fan-out or to
--   unpack it, and turning their quirks (a blank or sentinel start time, a
--   repeated id, inconsistent sub-event labels) into columns would put WTT's
--   labels and business rules in the landing schema. Deriving them is dbt's
--   job (ADR 0003).
--
-- WHAT IT IS NOT
--   Not a filter. It copies structure only, including empty team cards. There
--   is no DELETE or TRUNCATE anywhere: running it twice must be harmless.
--
-- FRESHNESS
--   first_seen_at is written once, on insert, and never touched again.
--   last_seen_at is rewritten on every unpack, so "still in the event" means
--   "last_seen_at moved". A match WTT dropped simply stops advancing.
--
-- NUMBERS TO EXPECT (measured 2026-11-10)
--   All 2838 landed payloads -> 200,879 rows, of which 2307 payloads hold no
--   matches at all and unpack to zero rows.
-- =============================================================================


INSERT INTO ingest.match_entries_unpacked (
    event_id,
    document_code,
    first_seen_at,
    last_seen_at
)
-- Naming the columns, rather than relying on their order, means adding a column
-- to the table later cannot quietly shift values into the wrong field.

SELECT DISTINCT ON (r.event_id, (e.value ->> 'documentCode'))

       r.event_id                                          AS event_id,
-- Taken from the raw row, not from the payload's "eventId": the raw row is what
-- was asked for, and every landed payload agrees with it (measured: 0
-- mismatches), so this cannot drift silently.

       (e.value ->> 'documentCode')::text                  AS document_code,
-- The match identity inside its event. A missing key yields NULL and the NOT
-- NULL column aborts the transaction, which is the loud failure we want.

       clock_timestamp()                                   AS first_seen_at,
-- Only meaningful on the first insert: the upsert below deliberately leaves it
-- alone, so it keeps recording when this match first appeared.

       clock_timestamp()                                   AS last_seen_at
-- Rewritten on every unpack; this is the column that moves while an event is
-- still running.

FROM ingest.match_entries_payloads_raw AS r
-- The raw row for the event we were asked for. The event id arrives as a query
-- parameter, filled in by Python, so one file serves every event.

CROSS JOIN LATERAL jsonb_array_elements(r.payload) AS e
-- The actual unpacking step: a bare array becomes one row per match.
-- LATERAL lets each row refer back to r from the line above. An empty array
-- (2307 payloads) simply yields no rows, which is a success. A payload that is
-- not an array at all raises here rather than silently unpacking to nothing.

WHERE r.event_id = %(event_id)s
-- One event per call. Python loops over the callers, each in its own
-- transaction, which is how landing already works. Keep exactly one placeholder
-- in the whole file: psycopg counts them literally, even inside a comment.

ORDER BY
    r.event_id,
    (e.value ->> 'documentCode')
-- DISTINCT ON requires ORDER BY to start with the same expressions, which both
-- lines do. document_code was never repeated inside a payload when measured, so
-- this pair is a complete, deterministic ordering with no tie-break needed.

ON CONFLICT (event_id, document_code) DO UPDATE SET
-- What happens when this match was already unpacked for this event: update it
-- instead of failing.
    last_seen_at = EXCLUDED.last_seen_at;
-- EXCLUDED means "the row we just tried to insert", so this reads "take the
-- newer presence stamp".
--
-- Deliberately absent: event_id and document_code (they are the key), and
-- first_seen_at. Leaving first_seen_at out is the whole point of the column;
-- rewriting it here would turn it into a second last_seen_at.

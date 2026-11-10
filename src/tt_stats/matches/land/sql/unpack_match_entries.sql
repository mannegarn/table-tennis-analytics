-- =============================================================================
-- unpack_match_entries.sql
--
-- Turns one event's WTT match list from JSON into one row per match.
--
-- WHAT "UNPACKING" MEANS HERE
--   The raw table holds the response exactly as it arrived: a bare JSON array
--   per event. You cannot filter, join or index inside an array, so the useful
--   fields are copied into real columns. That copy is this file. The array stays
--   put, so if the mapping is ever wrong the columns can be rebuilt from it.
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
--   match_id is still unpacked because the deferred match-detail fan-out looks
--   matches up by it, but it is a fact about the row, never its identity. It is
--   0 for the 183 team tie cards in event 2345.
--
-- id IS SPELLED "id" OR "iD"
--   854 rows in 7 payloads send "iD" instead of "id", never mixed inside one
--   payload. ``coalesce`` accepts whichever arrived, so the same statement
--   handles both and a rename to a third spelling fails loudly instead of
--   filling the column with NULLs.
--
-- BLANK TIMES BECOME NULL
--   424 rows send "" or nothing at all for startDateLocal. ``nullif(btrim(...))``
--   turns that into SQL NULL, which the column now allows. The sentinel
--   "0001-01-01T00:00:00" (1665 rows) is stored as sent: calling it "no date" is
--   a business rule and belongs in dbt.
--
-- WHAT IT IS NOT
--   Not a filter. It copies structure only, including empty team cards and rows
--   with no date. There is no DELETE or TRUNCATE anywhere: running it twice must
--   be harmless.
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
    match_id,
    event_id,
    document_code,
    sub_event_type,
    full_results,
    start_date_local,
    first_seen_at,
    last_seen_at
)
-- Naming the columns, rather than relying on their order, means adding a column
-- to the table later cannot quietly shift values into the wrong field.

SELECT DISTINCT ON (r.event_id, (e.value ->> 'documentCode'))

       coalesce(e.value ->> 'id', e.value ->> 'iD')::bigint AS match_id,
-- '->>' pulls a field out as text; cast to bigint because it is a number and
-- the fan-out will compare and join on it. coalesce covers the two spellings
-- upstream uses. If WTT invents a third, this yields NULL and the NOT NULL
-- column aborts the transaction, which is the loud failure we want.

       r.event_id                                          AS event_id,
-- Taken from the raw row, not from the payload's "eventId": the raw row is what
-- was asked for, and every landed payload agrees with it (measured: 0
-- mismatches), so this cannot drift silently.

       (e.value ->> 'documentCode')::text                  AS document_code,

       (e.value ->> 'subEventType')::text                  AS sub_event_type,
-- Human readable label as sent: "Men Singles", "Men's Team", ... No enum and no
-- mapping here, because the set of labels is WTT's to change.

       (e.value ->> 'fullResults')::text                   AS full_results,

       nullif(btrim(e.value ->> 'startDateLocal'), '')::timestamp
                                                           AS start_date_local,
-- Blank and missing both become NULL; a real timestamp, including one with
-- fractional seconds, is kept exactly as sent and with no timezone invented.

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
    (e.value ->> 'documentCode'),
    (e.value ->> 'startDateLocal') DESC NULLS LAST
-- DISTINCT ON requires ORDER BY to start with the same expressions, which the
-- first two lines do.
--
-- document_code was never repeated inside a payload when measured, so this
-- tie-break is insurance rather than a fact of the data: if WTT ever repeats a
-- code, the later-dated copy wins, deterministically, instead of Postgres
-- picking either row and the values flipping between runs. DESC NULLS LAST puts
-- a blank time after every real one.

ON CONFLICT (event_id, document_code) DO UPDATE SET
-- What happens when this match was already unpacked for this event: update it
-- instead of failing.
    match_id         = EXCLUDED.match_id,
    sub_event_type   = EXCLUDED.sub_event_type,
    full_results     = EXCLUDED.full_results,
    start_date_local = EXCLUDED.start_date_local,
    last_seen_at     = EXCLUDED.last_seen_at;
-- EXCLUDED means "the row we just tried to insert", so this reads "take the
-- newer values".
--
-- Deliberately absent: event_id and document_code (they are the key), and
-- first_seen_at. Leaving first_seen_at out is the whole point of the column;
-- rewriting it here would turn it into a second last_seen_at.

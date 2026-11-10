-- 006: the match-entries work queue, derived and never stored (ADR 0005).
--
-- WHAT IT ANSWERS
--   Which events deserve a GetOfficialResult request right now? One row per
--   event, in priority order, with a `reason` so a run of thousands of events
--   is debuggable from a log line alone.
--
-- THE RULES
--   p = current_date, w = end_date + 1 (a one-day grace, so an event that ended
--   yesterday still counts as live, and a fetch taken on the final day is not
--   treated as complete).
--
--     start_date > p                      -> not in the queue at all
--     p <= w                              -> live, fetched every run
--     finished, no payload                -> new, fetched once
--     finished, payload fetched_at::date <= w -> stale, fetched once more
--     finished, payload fetched after w   -> done, never fetched again
--
--   The grace is used by both the live test and the stale test, so the two
--   cannot disagree: an event cannot be "finished" and "possibly fetched mid-
--   tournament" at the same time.
--
-- NO YEAR FLOOR
--   Measured: WTT has no match data before late 2021 - eighteen sampled event
--   ids from 1930 to 2020 all return [], two bytes of it. A floor would save
--   ~2111 requests, but then "does WTT have anything for 1934?" could only be
--   answered by re-running a probe. Instead every event is asked once, the
--   empty answer is stored as a raw row with received_count = 0, and the
--   database becomes the evidence:
--
--     SELECT source_year, count(*) FROM ingest.events_unpacked
--     JOIN ingest.match_entries_payloads_raw USING (event_id)
--     WHERE received_count = 0 GROUP BY 1;
--
--   That also means an empty answer is never re-requested: row existence is what
--   marks an event as fetched.
--
-- ORDERING
--   Live first, then finished by most recent end date (ADR 0006: order the
--   queue, never filter it). A `--limit 5` smoke run therefore picks the
--   events whose data is still moving.

CREATE OR REPLACE VIEW ingest.match_entries_pending AS
WITH bounds AS (
    SELECT current_date AS today
)
SELECT
    e.event_id,
    e.event_name,
    e.start_date,
    e.end_date,
    CASE
        WHEN e.end_date + 1 >= b.today THEN 'live'
        WHEN r.event_id IS NULL THEN 'new'
        ELSE 'stale'
    END::text AS reason,
    r.fetched_at,
    r.received_count AS previous_received_count
FROM ingest.events_unpacked AS e
CROSS JOIN bounds AS b
LEFT JOIN ingest.match_entries_payloads_raw AS r ON r.event_id = e.event_id
WHERE e.start_date <= b.today
  AND (
        e.end_date + 1 >= b.today
     OR r.event_id IS NULL
     OR r.fetched_at::date <= e.end_date + 1
  );

COMMENT ON VIEW ingest.match_entries_pending IS
    'Events worth requesting now: live events always, unseen events once, and '
    'an event whose last snapshot predates its final day once more. Read-only, '
    'derived, never stored (ADR 0005).';
COMMENT ON COLUMN ingest.match_entries_pending.reason IS
    'live = in progress, keep refreshing; new = never fetched; stale = the '
    'last fetch happened on or before the final day of the event.';
COMMENT ON COLUMN ingest.match_entries_pending.previous_received_count IS
    'How many match rows the last stored payload held, NULL when never fetched. '
    'Useful for spotting an event that went from 0 to many.';

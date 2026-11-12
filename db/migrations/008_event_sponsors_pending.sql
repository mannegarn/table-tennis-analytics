-- 008: the event_sponsors work queue, derived and never stored (ADR 0005).
--
-- Answers which events deserve a GetEventEquipmentwithLogo request right now,
-- using the same eligibility rules as match_entries_pending:
--
--   p = today in UTC, w = GREATEST(start_date, end_date) + 1 (one-day grace)
--
--     start_date > p                         -> not in the queue
--     p <= w                                 -> live, fetched every run
--     finished, no payload                   -> new, fetched once
--     finished, payload fetched_at::date <= w -> stale, fetched once more
--     finished, payload fetched after w      -> done, never fetched again
--
-- Row existence in ingest.event_sponsors_raw (including empty [] payloads)
-- is what marks an event as fetched.

CREATE OR REPLACE VIEW ingest.event_sponsors_pending AS
WITH bounds AS (
    SELECT (current_timestamp AT TIME ZONE 'UTC')::date AS today
),
events AS (
    SELECT DISTINCT ON (e.event_id)
        e.event_id,
        e.event_name,
        e.start_date,
        e.end_date,
        GREATEST(e.start_date, e.end_date) + 1 AS window_end
    FROM ingest.events_unpacked AS e
    ORDER BY e.event_id, e.last_seen_at DESC, e.source_year DESC
)
SELECT
    e.event_id,
    e.event_name,
    e.start_date,
    e.end_date,
    CASE
        WHEN e.window_end >= b.today THEN 'live'
        WHEN r.event_id IS NULL THEN 'new'
        ELSE 'stale'
    END::text AS reason,
    r.fetched_at,
    r.received_count AS previous_received_count
FROM events AS e
CROSS JOIN bounds AS b
LEFT JOIN ingest.event_sponsors_raw AS r ON r.event_id = e.event_id
WHERE e.start_date <= b.today
  AND (
        e.window_end >= b.today
     OR r.event_id IS NULL
     OR (r.fetched_at AT TIME ZONE 'UTC')::date <= e.window_end
  );

COMMENT ON VIEW ingest.event_sponsors_pending IS
    'Events worth requesting sponsor details for now: live events always, '
    'unseen events once, and stale events once more after finishing (ADR 0005).';
COMMENT ON COLUMN ingest.event_sponsors_pending.reason IS
    'live = in progress, keep refreshing; new = never fetched; stale = the '
    'last fetch happened on or before the final day of the event.';
COMMENT ON COLUMN ingest.event_sponsors_pending.previous_received_count IS
    'How many sponsor rows the last stored payload held, NULL when never fetched.';

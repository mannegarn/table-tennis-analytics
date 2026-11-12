-- 011: the match_details work queue, derived and never stored (ADR 0005).
--
-- Answers which matches from ingest.match_entries_unpacked deserve a
-- matchdata/matchcentre request right now, using the event's dates from
-- ingest.events_unpacked:
--
--   p = today in UTC, w = GREATEST(start_date, end_date) + 1 (one-day grace)
--
--     start_date > p                         -> not in the queue
--     p <= w                                 -> live, fetched every run
--     finished, no payload                   -> new, fetched once
--     finished, payload fetched_at::date <= w -> stale, fetched once more
--     finished, payload fetched after w      -> done, never fetched again

CREATE OR REPLACE VIEW ingest.match_details_pending AS
WITH bounds AS (
    SELECT (current_timestamp AT TIME ZONE 'UTC')::date AS today
),
events AS (
    SELECT DISTINCT ON (e.event_id)
        e.event_id,
        e.start_date,
        e.end_date,
        GREATEST(e.start_date, e.end_date) + 1 AS window_end
    FROM ingest.events_unpacked AS e
    ORDER BY e.event_id, e.last_seen_at DESC, e.source_year DESC
)
SELECT
    m.event_id,
    m.document_code,
    e.start_date,
    e.end_date,
    CASE
        WHEN e.window_end >= b.today THEN 'live'
        WHEN r.event_id IS NULL THEN 'new'
        ELSE 'stale'
    END::text AS reason,
    r.fetched_at
FROM ingest.match_entries_unpacked AS m
JOIN events AS e ON e.event_id = m.event_id
CROSS JOIN bounds AS b
LEFT JOIN ingest.match_details_raw AS r
    ON r.event_id = m.event_id
   AND r.document_code = m.document_code
WHERE e.start_date <= b.today
  AND (
        e.window_end >= b.today
     OR r.event_id IS NULL
     OR (r.fetched_at AT TIME ZONE 'UTC')::date <= e.window_end
  );

COMMENT ON VIEW ingest.match_details_pending IS
    'Matches worth requesting details for now: live events always, unseen '
    'matches once, and stale matches once more after the event finishes.';

-- 013: the match_commentary work queue, derived and never stored (ADR 0005).

CREATE OR REPLACE VIEW ingest.match_commentary_pending AS
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
    r.fetched_at,
    r.received_count AS previous_received_count
FROM ingest.match_entries_unpacked AS m
JOIN events AS e ON e.event_id = m.event_id
CROSS JOIN bounds AS b
LEFT JOIN ingest.match_commentary_raw AS r
    ON r.event_id = m.event_id
   AND r.document_code = m.document_code
WHERE e.start_date <= b.today
  AND (
        e.window_end >= b.today
     OR r.event_id IS NULL
     OR (r.fetched_at AT TIME ZONE 'UTC')::date <= e.window_end
  );

COMMENT ON VIEW ingest.match_commentary_pending IS
    'Matches worth requesting commentary for now (ADR 0005).';

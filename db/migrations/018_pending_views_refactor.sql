-- 018: refactor pending views to share events_latest, add failure cooldowns,
-- and refresh player_details after 90 days (ADR 0005).

CREATE OR REPLACE VIEW ingest_wtt.events_latest AS
SELECT DISTINCT ON (e.event_id)
    e.event_id,
    e.event_name,
    e.start_date,
    e.end_date,
    GREATEST(e.start_date, e.end_date) + 1 AS window_end
FROM ingest_wtt.events_unpacked AS e
ORDER BY e.event_id, e.last_seen_at DESC, e.source_year DESC;

COMMENT ON VIEW ingest_wtt.events_latest IS
    'Latest unpacked sighting per event_id with the 1-day live-window grace date.';

CREATE OR REPLACE VIEW ingest_wtt.ingest_run_cooldowns AS
WITH recent_unrecovered_failures AS (
    SELECT
        r.endpoint,
        r.entity_key,
        r.finished_at
    FROM ingest_wtt.ingest_runs AS r
    WHERE
        r.status = 'failed'
        AND r.finished_at >= CURRENT_TIMESTAMP - INTERVAL '7 days'
        AND NOT EXISTS (
            SELECT 1
            FROM ingest_wtt.ingest_runs AS s
            WHERE
                s.endpoint = r.endpoint
                AND s.entity_key = r.entity_key
                AND s.status = 'succeeded'
                AND (
                    s.finished_at > r.finished_at
                    OR (
                        s.finished_at = r.finished_at
                        AND s.run_id > r.run_id
                    )
                )
        )
)

SELECT
    f.endpoint,
    f.entity_key,
    COUNT(*)::INT AS failure_count,
    MAX(f.finished_at) AS last_failed_at,
    MAX(f.finished_at) + CASE
        WHEN COUNT(*) = 1 THEN INTERVAL '1 hour'
        WHEN COUNT(*) = 2 THEN INTERVAL '4 hours'
        WHEN COUNT(*) = 3 THEN INTERVAL '24 hours'
        ELSE INTERVAL '7 days'
    END AS retry_after
FROM recent_unrecovered_failures AS f
GROUP BY f.endpoint, f.entity_key;

COMMENT ON VIEW ingest_wtt.ingest_run_cooldowns IS
    'Consecutive unrecovered failures in the last 7 days per (endpoint, entity_key) '
    'with exponential backoff retry_after timestamps (1h, 4h, 24h, 7d).';

CREATE OR REPLACE VIEW ingest_wtt.events_years_pending AS
WITH bounds AS (
    SELECT
        EXTRACT(YEAR FROM (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'))::INT
            AS current_year
)

SELECT
    y.year,
    CASE
        WHEN y.year = b.current_year THEN 'current'
        WHEN y.year = b.current_year + 1 THEN 'next'
        ELSE 'backfill'
    END::TEXT AS reason,
    r.fetched_at
FROM bounds AS b
CROSS JOIN GENERATE_SERIES(1926, b.current_year + 1) AS y (year)
LEFT JOIN ingest_wtt.events_by_year_raw AS r ON r.year = y.year
LEFT JOIN ingest_wtt.ingest_run_cooldowns AS c
    ON
        c.endpoint = 'wtt_eventcalendar'
        AND c.entity_key = y.year::TEXT
        AND c.retry_after > CURRENT_TIMESTAMP
WHERE
    (
        y.year IN (b.current_year, b.current_year + 1)
        OR r.year IS NULL
    )
    AND c.entity_key IS NULL;

COMMENT ON VIEW ingest_wtt.events_years_pending IS
    'Years worth requesting now, excluding years in failure cooldown: current and '
    'next year always, past years only while their payload is missing (ADR 0005).';

CREATE OR REPLACE VIEW ingest_wtt.match_entries_pending AS
WITH bounds AS (
    SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::DATE AS today
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
    END::TEXT AS reason,
    r.fetched_at,
    r.received_count AS previous_received_count
FROM ingest_wtt.events_latest AS e
CROSS JOIN bounds AS b
LEFT JOIN ingest_wtt.match_entries_payloads_raw AS r ON r.event_id = e.event_id
LEFT JOIN ingest_wtt.ingest_run_cooldowns AS c
    ON
        c.endpoint = 'wtt_getofficialresult'
        AND c.entity_key = e.event_id::TEXT
        AND c.retry_after > CURRENT_TIMESTAMP
WHERE
    e.start_date <= b.today
    AND (
        e.window_end >= b.today
        OR r.event_id IS NULL
        OR (r.fetched_at AT TIME ZONE 'UTC')::DATE <= e.window_end
    )
    AND c.entity_key IS NULL;

COMMENT ON VIEW ingest_wtt.match_entries_pending IS
    'Events worth requesting match entries for now, excluding events in failure '
    'cooldown (ADR 0005).';

CREATE OR REPLACE VIEW ingest_wtt.event_sponsors_pending AS
WITH bounds AS (
    SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::DATE AS today
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
    END::TEXT AS reason,
    r.fetched_at,
    r.received_count AS previous_received_count
FROM ingest_wtt.events_latest AS e
CROSS JOIN bounds AS b
LEFT JOIN ingest_wtt.event_sponsors_raw AS r ON r.event_id = e.event_id
LEFT JOIN ingest_wtt.ingest_run_cooldowns AS c
    ON
        c.endpoint = 'wtt_geteventequipmentwithlogo'
        AND c.entity_key = e.event_id::TEXT
        AND c.retry_after > CURRENT_TIMESTAMP
WHERE
    e.start_date <= b.today
    AND (
        e.window_end >= b.today
        OR r.event_id IS NULL
        OR (r.fetched_at AT TIME ZONE 'UTC')::DATE <= e.window_end
    )
    AND c.entity_key IS NULL;

COMMENT ON VIEW ingest_wtt.event_sponsors_pending IS
    'Events worth requesting sponsor details for now, excluding events in failure '
    'cooldown (ADR 0005).';

CREATE OR REPLACE VIEW ingest_wtt.match_details_pending AS
WITH bounds AS (
    SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::DATE AS today
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
    END::TEXT AS reason,
    r.fetched_at
FROM ingest_wtt.match_entries_unpacked AS m
INNER JOIN ingest_wtt.events_latest AS e ON e.event_id = m.event_id
CROSS JOIN bounds AS b
LEFT JOIN ingest_wtt.match_details_raw AS r
    ON
        r.event_id = m.event_id
        AND r.document_code = m.document_code
LEFT JOIN ingest_wtt.ingest_run_cooldowns AS c
    ON
        c.endpoint = 'wtt_matchcentre'
        AND c.entity_key = m.event_id::TEXT || ':' || m.document_code
        AND c.retry_after > CURRENT_TIMESTAMP
WHERE
    e.start_date <= b.today
    AND (
        e.window_end >= b.today
        OR r.event_id IS NULL
        OR (r.fetched_at AT TIME ZONE 'UTC')::DATE <= e.window_end
    )
    AND c.entity_key IS NULL;

COMMENT ON VIEW ingest_wtt.match_details_pending IS
    'Matches worth requesting details for now, excluding matches in failure '
    'cooldown (ADR 0005).';

CREATE OR REPLACE VIEW ingest_wtt.match_commentary_pending AS
WITH bounds AS (
    SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::DATE AS today
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
    END::TEXT AS reason,
    r.fetched_at,
    r.received_count AS previous_received_count
FROM ingest_wtt.match_entries_unpacked AS m
INNER JOIN ingest_wtt.events_latest AS e ON e.event_id = m.event_id
CROSS JOIN bounds AS b
LEFT JOIN ingest_wtt.match_commentary_raw AS r
    ON
        r.event_id = m.event_id
        AND r.document_code = m.document_code
LEFT JOIN ingest_wtt.ingest_run_cooldowns AS c
    ON
        c.endpoint = 'wtt_match_commentary'
        AND c.entity_key = m.event_id::TEXT || ':' || m.document_code
        AND c.retry_after > CURRENT_TIMESTAMP
WHERE
    e.start_date <= b.today
    AND (
        e.window_end >= b.today
        OR r.event_id IS NULL
        OR (r.fetched_at AT TIME ZONE 'UTC')::DATE <= e.window_end
    )
    AND c.entity_key IS NULL;

COMMENT ON VIEW ingest_wtt.match_commentary_pending IS
    'Matches worth requesting commentary for now, excluding matches in failure '
    'cooldown (ADR 0005).';

CREATE OR REPLACE VIEW ingest_wtt.player_details_pending AS
WITH seen_players AS (
    SELECT DISTINCT p.player_id
    FROM ingest_wtt.match_details_players_unpacked AS p
)

SELECT
    sp.player_id,
    CASE
        WHEN r.player_id IS NULL THEN 'new'
        ELSE 'stale'
    END::TEXT AS reason,
    r.fetched_at
FROM seen_players AS sp
LEFT JOIN ingest_wtt.player_details_raw AS r
    ON r.player_id = sp.player_id
LEFT JOIN ingest_wtt.ingest_run_cooldowns AS c
    ON
        c.endpoint = 'wtt_player_details'
        AND c.entity_key = sp.player_id::TEXT
        AND c.retry_after > CURRENT_TIMESTAMP
WHERE
    (
        r.player_id IS NULL
        OR r.fetched_at < CURRENT_TIMESTAMP - INTERVAL '90 days'
    )
    AND c.entity_key IS NULL;

COMMENT ON VIEW ingest_wtt.player_details_pending IS
    'Players seen in match_details_players_unpacked that are unseen (new) or '
    'older than 90 days (stale), excluding players in failure cooldown (ADR 0005).';

-- 004: the event-year work queue, derived and never stored (ADR 0005).
--
-- WHAT IT ANSWERS
--   Which calendar years deserve a request right now? One row per candidate
--   year, with a `reason` so a collect run can say why it asked.
--
-- THE RULES
--   cy = current year, p = current_date.
--
--     year = cy            always      -> current, the calendar keeps changing
--     year = cy + 1        always      -> next, WTT publishes it during the year
--     year < cy, no payload            -> backfill, asked once
--     year < cy, payload landed        -> never again
--     year > cy + 1                    -> not offered at all
--
--   The candidate series starts at 1900 and the collector applies its own floor
--   (`Settings.events_start_year`, currently 1926, the first year the API has
--   any tournaments for). Keeping the floor in configuration means widening or
--   narrowing a backfill is a setting, not a migration.
--
-- WHY A YEAR IS NEVER REFETCHED ONCE PAST
--   A past year's calendar is static, so one landing is enough. The accepted
--   cost is that an upstream correction found later is missed; the escape hatch
--   is `tt-events-collect --years 2023`, which bypasses this view.
--
-- ORDERING
--   The current year first, then next year, then the backfill newest-first.
--   `--limit 5` is therefore "this year, next year, then recent history" rather
--   than an arbitrary slice.

CREATE OR REPLACE VIEW ingest.events_years_pending AS
WITH bounds AS (
    SELECT EXTRACT(YEAR FROM current_date)::int AS current_year
)
SELECT
    y.year,
    CASE
        WHEN y.year = b.current_year THEN 'current'
        WHEN y.year = b.current_year + 1 THEN 'next'
        ELSE 'backfill'
    END::text AS reason,
    r.fetched_at
FROM bounds AS b
CROSS JOIN LATERAL generate_series(1900, b.current_year + 1) AS y (year)
LEFT JOIN ingest.events_by_year_raw AS r ON r.year = y.year
WHERE y.year IN (b.current_year, b.current_year + 1)
   OR r.year IS NULL;

COMMENT ON VIEW ingest.events_years_pending IS
    'Years worth requesting now: the current and next year always, any other '
    'year only while its payload is missing. Read-only, derived, never stored '
    '(ADR 0005).';
COMMENT ON COLUMN ingest.events_years_pending.year IS
    'Calendar year to request. Callers apply their own floor, normally '
    'Settings.events_start_year.';
COMMENT ON COLUMN ingest.events_years_pending.reason IS
    'current = this year, keep refreshing; next = next year, keep refreshing; '
    'backfill = a past year whose payload has never landed.';
COMMENT ON COLUMN ingest.events_years_pending.fetched_at IS
    'When the landed payload for this year was fetched, NULL for a backfill '
    'candidate. Informational: eligibility does not depend on it.';

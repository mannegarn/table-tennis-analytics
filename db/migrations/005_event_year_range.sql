-- 006: let the calendar reach back to the 1920s, and publish the year queue.
--
-- WHY
--   The API's first year with tournaments is 1926 (measured with
--   scripts/probe_events_by_year.py: 102 years swept, 2984 calendar rows, nine
--   empty years). The original constraints only allowed 2000-2100, so a
--   historical backfill would fail on its first insert.
--
--   A new migration rather than an edit: apply_migrations() fingerprints each
--   file and refuses a changed checksum on an already-applied version, which is
--   exactly what stops the on-disk history disagreeing with the database.
--
-- WHY 1900 AND NOT 1926
--   The check is a sanity bound, not a policy. Policy lives in the view and in
--   settings.events_start_year, so the constraint only has to catch an absurd
--   value (a typo, a string cast, a year 100000). Keeping it wider than the
--   data means the floor can move without another migration.
--
-- THE VIEW
--   ingest.events_years_pending answers "which years deserve a request now?",
--   the same way match_entries_pending will answer it for events (ADR 0005:
--   eligibility is derived, never stored):
--
--     current year       always, because the calendar keeps changing
--     next year          always, because WTT publishes it during the year
--     any other year     only when its payload is missing (a backfill)
--
--   A year that already landed and is now in the past is never fetched again.
--   The cost is that an upstream correction discovered later is missed; the
--   escape hatch is `tt-events-collect --years 2023`, which bypasses the view.
--
--   The view starts its candidate series at 1900 and the collector applies
--   `year >= events_start_year`, so the floor stays configuration rather than
--   hard-coded SQL.

ALTER TABLE ingest.events_by_year_raw
    DROP CONSTRAINT IF EXISTS events_by_year_raw_year_check;
ALTER TABLE ingest.events_by_year_raw
    ADD CONSTRAINT events_by_year_raw_year_check
    CHECK (year BETWEEN 1900 AND 2100);

ALTER TABLE ingest.events_unpacked
    DROP CONSTRAINT IF EXISTS events_unpacked_source_year_check;
ALTER TABLE ingest.events_unpacked
    ADD CONSTRAINT events_unpacked_source_year_check
    CHECK (source_year BETWEEN 1900 AND 2100);

COMMENT ON CONSTRAINT events_by_year_raw_year_check
    ON ingest.events_by_year_raw IS
    'Sanity bound only. Real coverage starts at 1926; the backfill floor is '
    'Settings.events_start_year.';
COMMENT ON CONSTRAINT events_unpacked_source_year_check
    ON ingest.events_unpacked IS
    'Sanity bound only. Real coverage starts at 1926.';

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

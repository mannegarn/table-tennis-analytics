-- 006: stop rejecting an event whose end date precedes its start date.
--
-- WHY
--   The 1926-2027 backfill landed 101 of 102 years. The one failure was 1992,
--   and it was not a fetch or unpack problem:
--
--     year=1992 ... error=landing failed: violates check constraint
--     "events_unpacked_dates_check"
--     DETAIL: Failing row contains (1992, 1156, " World Cup  Ho Chi Minh City",
--                                   1992-12-10, 1991-12-13, ...)
--
--   Upstream sends EndDateTime earlier than StartDateTime for that event. The
--   check then aborted the whole transaction, so 15 otherwise-good 1992 events
--   were thrown away with it, and the year could never land at all.
--
-- WHY REMOVING IT IS THE CORRECT FIX, NOT LOOSENING THE DATA
--   ADR 0003 says the unpack records **structure only**: JSON to columns, with
--   no filtering and no domain rules. "An event cannot end before it starts" is
--   a domain rule. Enforcing it at land time made ingestion the judge of data
--   quality, and the cost was a silent hole in the archive.
--
--   The raw payload is preserved either way, so the bad row is fully
--   recoverable. Nothing is hidden: it is visible as a single row with inverted
--   dates, which is exactly what a dbt test is for (`end_date >= start_date`),
--   where it can be flagged or excluded without destroying its neighbours.
--
-- WHAT IS KEPT
--   events_unpacked_name_check and the year sanity bound stay. A blank name or
--   a year of 1899 still points at a broken unpack rather than at upstream
--   data, which is the distinction that matters: fail when our mapping is
--   wrong, record when theirs is odd.

ALTER TABLE ingest.events_unpacked
    DROP CONSTRAINT IF EXISTS events_unpacked_dates_check;

COMMENT ON TABLE ingest.events_unpacked IS
    'One row per WTT event per calendar, unpacked from '
    'ingest.events_by_year_raw. Structure only: no filtering and no domain '
    'rules, so an upstream inverted date range is stored as sent and flagged '
    'in dbt rather than rejected here (ADR 0003).';

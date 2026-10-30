-- 004: clearer count names.
--
-- The three count columns were ambiguous: "api_count" read like "number of API
-- calls", when it is actually the Count field inside WTT's payload. Two of the
-- three numbers belong to WTT and only one is ours, so the names now say which.
--
--   original_count  -> source_original_count   (WTT's Original_Count)
--   api_count       -> source_final_count      (WTT's Count)
--   row_count       -> received_count          (rows we actually parsed)
--
-- Renames only. No data is rewritten, so the existing rows are untouched.

ALTER TABLE ingest.events_by_year_raw
    RENAME COLUMN original_count TO source_original_count;

ALTER TABLE ingest.events_by_year_raw
    RENAME COLUMN api_count TO source_final_count;

ALTER TABLE ingest.events_by_year_raw
    RENAME COLUMN row_count TO received_count;

-- Constraint names carry the old column name, so rename them too; otherwise a
-- future error message points at a column that no longer exists.
ALTER TABLE ingest.events_by_year_raw
    RENAME CONSTRAINT events_by_year_raw_row_count_check
                 TO events_by_year_raw_received_count_check;

-- The run table counts the same thing for one attempt, so it gets the same name.
ALTER TABLE ingest.ingest_runs
    RENAME COLUMN row_count TO received_count;

ALTER TABLE ingest.ingest_runs
    RENAME CONSTRAINT ingest_runs_row_count_check
                 TO ingest_runs_received_count_check;

COMMENT ON COLUMN ingest.events_by_year_raw.source_original_count IS
    'WTT Original_Count: their total before their own internal step. '
    'Undocumented upstream, always >= source_final_count, informational only.';
COMMENT ON COLUMN ingest.events_by_year_raw.source_final_count IS
    'WTT Count: how many rows the API says it returned. '
    'Verified against received_count before a year is accepted.';
COMMENT ON COLUMN ingest.events_by_year_raw.received_count IS
    'Rows we parsed from payload -> 0 -> rows. Ours, not theirs. '
    'A mismatch against source_final_count fails the year.';
COMMENT ON COLUMN ingest.ingest_runs.received_count IS
    'Rows this attempt produced. Null or 0 for a failed attempt.';

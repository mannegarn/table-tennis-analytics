-- 006: every operational column on ingest.events_unpacked is NOT NULL.
--
-- Why this migration exists even though 003 and 005 already declared most of
-- these columns NOT NULL: the unpack statement pulls values out of JSON with
-- `->>`, and a missing JSON key yields SQL NULL instead of an error. Postgres
-- will happily accept a NULL event_name or start_date unless the column itself
-- refuses it. These constraints are therefore the backstop that turns an
-- upstream rename ("EventName" -> "Event_Name") into a failed transaction
-- instead of a quietly corrupt row (ADR 0003: fail loudly on schema drift).
--
-- All six columns are non-nullable by contract:
--
--   source_year    half of the primary key, and the value the unpack filters on
--   event_id       the other half of the key; also the WTT join key
--   event_name     read by every downstream model, blank names are already banned
--   start_date     orders events and anchors the season calculation
--   first_seen_at  records when the row first appeared; written once
--   last_seen_at   records presence; a NULL would read as "never seen"
--
-- end_date stays NOT NULL as well. EndDateTime is part of the documented event
-- contract and is present in every landed payload (see 003), so a NULL there is
-- drift, not a one-day tournament. If WTT ever starts omitting it, relax this
-- with a new migration rather than by quietly allowing NULLs now.
--
-- If an existing row already violates one of these, this migration fails and
-- says which column: that is the intended outcome. Fix the unpack statement and
-- re-run; do not delete the evidence.
--
-- SET NOT NULL is idempotent, so re-running this file is harmless.

ALTER TABLE ingest.events_unpacked
    ALTER COLUMN source_year SET NOT NULL,
    ALTER COLUMN event_id SET NOT NULL,
    ALTER COLUMN event_name SET NOT NULL,
    ALTER COLUMN start_date SET NOT NULL,
    ALTER COLUMN end_date SET NOT NULL,
    ALTER COLUMN first_seen_at SET NOT NULL,
    ALTER COLUMN last_seen_at SET NOT NULL;

COMMENT ON COLUMN ingest.events_unpacked.event_name IS
    'EventName as sent, trimmed only by the NOT NULL and non-blank checks. '
    'A NULL here means the unpack statement stopped finding the upstream key.';
COMMENT ON COLUMN ingest.events_unpacked.start_date IS
    'StartDateTime as a date. The API sends no timezone, so none is invented. '
    'NOT NULL: a missing StartDateTime is schema drift, not a valid event.';
COMMENT ON COLUMN ingest.events_unpacked.end_date IS
    'EndDateTime as a date. Drives the still-live window in the pending view. '
    'NOT NULL because EndDateTime is part of the documented payload contract.';

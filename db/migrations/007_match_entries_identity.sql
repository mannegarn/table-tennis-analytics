-- 007: the unpacked match entries are keyed by what actually identifies a match.
--
-- WHY THIS IS AN ADDITIVE MIGRATION AND NOT AN AMENDMENT OF 005
--   005 assumed ``match_id`` was the identity: one row per match, keyed on the
--   upstream id. Measurement on 2026-11-10 falsified that before any unpack ran,
--   but 2838 payloads were already landed in the development database. Amending
--   005 would change its checksum, and ``ingest.schema_version`` refuses a
--   changed file. Rebuilding would mean re-fetching every match list. This is
--   the moment the plan said would come: the database is now worth preserving,
--   so additive migrations start here.
--
-- MEASURED, 2838 payloads / 200,879 match rows
--   distinct (event_id, document_code) pairs ....... 200,879
--   document_code repeated inside one event ....... 0
--   match id repeated inside one event ............ 278 groups, 943 rows, each
--                                                   with a DIFFERENT documentCode
--                                                   (e.g. event 2411 match 59820
--                                                   arrives as QFNL000100 and
--                                                   QFNL000200)
--   one match id under two or three events ....... 132
--                                                   (events 2553/2554, 2632/2634)
--   "id" spelled "iD" ............................. 854 rows, 7 payloads, never
--                                                   mixed inside one payload
--   id = 0 ........................................ 183 rows, all in team event
--                                                   2345, all with blank
--                                                   startDateLocal
--   startDateLocal blank or missing ............... 424 rows
--   startDateLocal "0001-01-01T00:00:00" .......... 1665 rows (WTT's null sentinel)
--   documentCode length ............................ 42 (198,579) or 34 (2,300)
--
-- WHY document_code IS THE MATCH IDENTITY
--   It is the only field that is unique within an event: TTEM + sub-event +
--   round + match number. ``id`` repeats inside one event for genuinely
--   different matches, and repeats across events for different tournaments, so
--   it can neither be the key nor be assumed global. Keying on
--   (event_id, document_code) also means a fifth-place match cannot overwrite a
--   quarter-final because WTT reused an id.
--
-- WHY match_id KEEPS A ROW WHEN IT IS 0
--   Event 2345 sends 183 team tie cards with no id and no date. They are not
--   fetchable downstream, but they are what the API said, and the raw payload
--   is the evidence. Rejecting them would silently drop an event's whole match
--   list; storing them keeps the unpack faithful and lets dbt decide. The check
--   therefore allows 0 and refuses only negatives.
--
-- WHY start_date_local BECOMES NULLABLE
--   424 rows carry no start time at all. The alternative was to invent one or
--   to drop those matches, and both are worse than an honest NULL. The sentinel
--   "0001-01-01" is stored as sent, not translated: interpreting it as "no
--   date" is a business rule, and business rules belong in dbt (ADR 0003).
--
-- WHAT HAPPENS TO THE OLD KEY
--   The primary key on match_id is dropped. A plain index on match_id replaces
--   it, because the deferred match-detail fan-out will still look matches up by
--   that id (ADR 0006, ADR 0009). The event_id index is dropped too: the new
--   primary key already leads with event_id.

ALTER TABLE ingest.match_entries_unpacked
    DROP CONSTRAINT IF EXISTS match_entries_unpacked_pkey;

ALTER TABLE ingest.match_entries_unpacked
    ADD CONSTRAINT match_entries_unpacked_pkey
    PRIMARY KEY (event_id, document_code);

ALTER TABLE ingest.match_entries_unpacked
    DROP CONSTRAINT IF EXISTS match_entries_unpacked_match_id_check;

ALTER TABLE ingest.match_entries_unpacked
    ADD CONSTRAINT match_entries_unpacked_match_id_check
    CHECK (match_id >= 0);

ALTER TABLE ingest.match_entries_unpacked
    ALTER COLUMN start_date_local DROP NOT NULL;

-- Folded into the new primary key's leading column.
DROP INDEX IF EXISTS ingest.match_entries_unpacked_event_id_idx;

-- The fan-out to match details reads one row per match id.
CREATE INDEX IF NOT EXISTS match_entries_unpacked_match_id_idx
    ON ingest.match_entries_unpacked (match_id);

COMMENT ON TABLE ingest.match_entries_unpacked IS
    'One row per WTT match per event, unpacked from '
    'ingest.match_entries_payloads_raw. Match ENTRIES: identifies matches, '
    'does not carry their details. Keyed on (event_id, document_code): the '
    'upstream id repeats inside and across events (ADR 0010).';
COMMENT ON COLUMN ingest.match_entries_unpacked.match_id IS
    'Payload key "id" or "iD", renamed to say what it is, cast to bigint. A '
    'FACT, not the identity: it repeats inside one event for different matches '
    'and is 0 for team tie cards. Use (event_id, document_code) to identify a '
    'match.';
COMMENT ON COLUMN ingest.match_entries_unpacked.event_id IS
    'Payload key "eventId". A STRING upstream, cast to bigint here so it joins '
    'to events_unpacked.event_id without a cast on every query. Leads the key.';
COMMENT ON COLUMN ingest.match_entries_unpacked.document_code IS
    'Payload key "documentCode", the match identity: a composite such as '
    'TTEMSINGLES-----------FNL-000100---------- (42 chars) or the shorter team '
    'form (34 chars). Unique within an event and never seen twice in 200,879 '
    'rows. Kept intact; splitting it into gender, draw and round is a business '
    'rule and belongs in dbt.';
COMMENT ON COLUMN ingest.match_entries_unpacked.start_date_local IS
    'Payload key "startDateLocal" as timestamp WITHOUT time zone. The API sends '
    'no offset, so none is invented. NULL when the payload sent blank or no '
    'value: 424 rows, plus every team tie card in event 2345.';

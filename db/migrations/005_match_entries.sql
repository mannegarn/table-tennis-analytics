-- 005: the match-entries domain, owned by ingestion.
--
-- Two tables, the same pair as the events domain:
--
--   ingest.match_entries_payloads_raw  one row per EVENT (one API request)
--   ingest.match_entries_unpacked      one row per MATCH
--
-- WHY THE RAW TABLE IS PER EVENT, NOT PER MATCH
--   The endpoint is GET /api/cms/GetOfficialResult?EventId=<id>&DocumentCode=TTE
--   and it returns the whole match list for one tournament. So the request
--   grain is the event, and the container table is named for that grain
--   (ADR 0003: container names describe the request, unpacked names describe
--   the entity). One row per event is also exactly the "already fetched?"
--   signal the queue needs: row exists means the event's matches were fetched.
--
-- MEASURED CONTRACT (event 3098, 2025, five sub-events, 306 matches)
--   The response is a BARE LIST of 306 objects, 75,911 bytes, not the
--   calendar's [{"rows": [...]}] envelope. Every row carries:
--
--     id              int    match id, unique in this payload (306 distinct)
--     eventId         str    "3098", a STRING although the calendar sends int
--     documentCode    str    42 chars: TTEM + SINGLES/DOUBLES + round + match no.
--     subEventType    str    "Men Singles", "Women Singles", "Mixed Doubles", ...
--     fullResults     str    "OFFICIAL" for every row seen
--     startDateLocal  str    "2025-09-29T18:35:00", no timezone
--     match_card      null   empty here; enrichment slot
--     extended_info   null   empty here; enrichment slot
--     messagePayload  null   empty here; enrichment slot
--
--   The three null keys are the reason this table is called match ENTRIES and
--   not match DETAILS: this call is an index. The per-match fan-out that fills
--   those slots is a separate endpoint and is deferred (ADR 0006). Landing the
--   nulls is correct - they are what the API sent - and a column is only added
--   for them when a call actually populates them.
--
-- WHY THERE ARE NO source_*_count COLUMNS
--   The calendar envelope states its own count (Count / Original_Count), so
--   events_by_year_raw keeps both for cross-checking. This payload declares no
--   count at all, so received_count is the only number that exists and the only
--   one that can be compared with what the unpack writes.
--
-- WHY THE KEY IS (event_id, document_code), NOT match id
--   Measured on the 200,879 rows landed before the unpack existed:
--
--     distinct (event_id, documentCode) pairs .. 200,879, zero repeats, so the
--                                                document code identifies a
--                                                match inside its event
--     match id repeated inside one event ....... 278 groups, 943 rows, each with
--                                                a DIFFERENT documentCode
--                                                (event 2411 sends id 59820 as
--                                                both QFNL000100 and QFNL000200)
--     one match id under two or three events ... 132 (events 2553/2554, 2632/2634)
--     match id = 0 ............................ 183 team tie cards in event 2345
--     match id spelled "iD" ................... 854 rows, 7 payloads, never mixed
--                                                inside one payload
--     documentCode length ..................... 42 (198,579) or 34 (2,300)
--
--   So the id is neither unique nor global: it can only be a fact about a match.
--   It is still unpacked because it is the field people mean by "the match", but
--   the fan-out does not need it: match details and logs are fetched by
--   (eventId, documentCode) (ADR 0009). ADR 0010 records the decision.
--
-- WHY THE UNPACKED TABLE IS THIS NARROW
--   Only what the pipeline needs without reading JSON: the identity, the
--   presence timestamps, the upstream id and the start time. Everything else
--   stays in the payload and is dbt's to derive.
--
--     full_results   "OFFICIAL" on all 200,879 rows, so it carries no signal.
--                    if it ever varies, the payload still has it.
--     subEventType   derivable from document_code (TTEM/TTEW + SINGLES/
--                    DOUBLES/TEAM), and its labels are inconsistent upstream
--                    ("Men Singles" next to "Men's Team"). Splitting the code is
--                    a business rule anyway (ADR 0003), so dbt owns both.
--
-- NOT NULL
--   Same rule as the events domain: ->> yields SQL NULL for a renamed key
--   instead of raising, so non-nullable columns turn upstream drift into a
--   failed transaction rather than a silently corrupt row (ADR 0003).
--
--   start_date_local is the one exception: 424 rows send blank or no time at
--   all, so the column allows NULL and the unpack writes NULL rather than
--   inventing a time. The "0001-01-01T00:00:00" sentinel (1665 rows) is stored
--   as sent, because calling it "unknown" is a business rule and belongs in dbt.
--
-- NO FOREIGN KEY TO events_unpacked
--   That table is keyed (source_year, event_id), so event_id alone is not a
--   unique target. The link is by value and indexed instead.

CREATE TABLE IF NOT EXISTS ingest.match_entries_payloads_raw (
    event_id bigint PRIMARY KEY,
    payload jsonb NOT NULL,
    http_status integer NOT NULL,
    received_count integer NOT NULL,
    payload_sha256 text NOT NULL,
    source text NOT NULL DEFAULT 'wtt_getofficialresult',
    fetched_at timestamptz NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT match_entries_payloads_raw_event_id_check
        CHECK (event_id > 0),
    CONSTRAINT match_entries_payloads_raw_status_check
        CHECK (http_status BETWEEN 100 AND 599),
    CONSTRAINT match_entries_payloads_raw_received_count_check
        CHECK (received_count >= 0),
    CONSTRAINT match_entries_payloads_raw_sha256_check
        CHECK (payload_sha256 ~ '^[0-9a-f]{64}$')
);

COMMENT ON TABLE ingest.match_entries_payloads_raw IS
    'One raw WTT GetOfficialResult response per event, with fetch provenance.';
COMMENT ON COLUMN ingest.match_entries_payloads_raw.event_id IS
    'WTT EventId this match list belongs to. One row per event, so this is the key.';
COMMENT ON COLUMN ingest.match_entries_payloads_raw.payload IS
    'Response body as JSONB: a bare array of match rows, not a rows envelope.';
COMMENT ON COLUMN ingest.match_entries_payloads_raw.received_count IS
    'Rows parsed from the payload, i.e. len(payload). Ours. The API declares no '
    'count of its own, so this is the only total that exists.';
COMMENT ON COLUMN ingest.match_entries_payloads_raw.payload_sha256 IS
    'SHA-256 of the original response bytes.';
COMMENT ON COLUMN ingest.match_entries_payloads_raw.source IS
    'Endpoint that produced the payload. Separate from the events calendar source.';
COMMENT ON COLUMN ingest.match_entries_payloads_raw.fetched_at IS
    'When the request completed.';
COMMENT ON COLUMN ingest.match_entries_payloads_raw.ingested_at IS
    'When the row was written.';


CREATE TABLE IF NOT EXISTS ingest.match_entries_unpacked (
    match_id bigint NOT NULL,
    event_id bigint NOT NULL,
    document_code text NOT NULL,
    start_date_local timestamp,
    first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT match_entries_unpacked_pkey PRIMARY KEY (event_id, document_code),
    CONSTRAINT match_entries_unpacked_match_id_check
        CHECK (match_id >= 0),
    CONSTRAINT match_entries_unpacked_event_id_check
        CHECK (event_id > 0),
    CONSTRAINT match_entries_unpacked_document_code_check
        CHECK (btrim(document_code) <> '')
);

-- The pending view and the fan-out read by event, and the primary key already
-- leads with event_id, so no separate event index is needed.

COMMENT ON TABLE ingest.match_entries_unpacked IS
    'One row per WTT match per event, unpacked from '
    'ingest.match_entries_payloads_raw. Match ENTRIES: identifies matches, '
    'does not carry their details. Keyed on (event_id, document_code): the '
    'upstream id repeats inside and across events (ADR 0010). Narrow on '
    'purpose: identity, id, start time and presence only.';
COMMENT ON COLUMN ingest.match_entries_unpacked.match_id IS
    'Payload key "id" or "iD", renamed to say what it is, cast to bigint. A '
    'FACT, not the identity: it repeats inside one event for different matches '
    'and is 0 for team tie cards. Use (event_id, document_code) to identify a '
    'match, and note the fan-out keys on that pair (ADR 0009).';
COMMENT ON COLUMN ingest.match_entries_unpacked.event_id IS
    'Payload key "eventId". A STRING upstream, cast to bigint here so it joins '
    'to events_unpacked.event_id without a cast on every query. Leads the key '
    'and is half of the fan-out key.';
COMMENT ON COLUMN ingest.match_entries_unpacked.document_code IS
    'Payload key "documentCode", the match identity and the other half of the '
    'fan-out key: a composite such as '
    'TTEMSINGLES-----------FNL-000100---------- (42 chars) or the shorter team '
    'form (34 chars). Unique within an event and never seen twice in 200,879 '
    'rows. Kept intact; splitting it into gender, draw and round is a business '
    'rule and belongs in dbt.';
COMMENT ON COLUMN ingest.match_entries_unpacked.start_date_local IS
    'Payload key "startDateLocal" as timestamp WITHOUT time zone. The API sends '
    'no offset, so none is invented. NULL when the payload sent blank or no '
    'value: 424 rows, plus every team tie card in event 2345.';
COMMENT ON COLUMN ingest.match_entries_unpacked.first_seen_at IS
    'When this match_id was first unpacked. Written once.';
COMMENT ON COLUMN ingest.match_entries_unpacked.last_seen_at IS
    'When this match was last present in its event payload. Advances on every '
    'unpack, so a value that stops moving means WTT dropped the match.';

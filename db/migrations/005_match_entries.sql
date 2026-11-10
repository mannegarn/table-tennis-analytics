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
-- NOT NULL
--   Same rule as the events domain: ->> yields SQL NULL for a renamed key
--   instead of raising, so non-nullable columns turn upstream drift into a
--   failed transaction rather than a silently corrupt row (ADR 0003).
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
    sub_event_type text NOT NULL,
    full_results text NOT NULL,
    start_date_local timestamp NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT match_entries_unpacked_pkey PRIMARY KEY (match_id),
    CONSTRAINT match_entries_unpacked_match_id_check
        CHECK (match_id > 0),
    CONSTRAINT match_entries_unpacked_event_id_check
        CHECK (event_id > 0),
    CONSTRAINT match_entries_unpacked_document_code_check
        CHECK (btrim(document_code) <> ''),
    CONSTRAINT match_entries_unpacked_sub_event_type_check
        CHECK (btrim(sub_event_type) <> ''),
    CONSTRAINT match_entries_unpacked_full_results_check
        CHECK (btrim(full_results) <> '')
);

-- The fan-out to match details and the pending view both read by event.
CREATE INDEX IF NOT EXISTS match_entries_unpacked_event_id_idx
    ON ingest.match_entries_unpacked (event_id);

-- Reporting per draw ("how many Men Doubles matches") reads this pair.
CREATE INDEX IF NOT EXISTS match_entries_unpacked_event_sub_event_idx
    ON ingest.match_entries_unpacked (event_id, sub_event_type);

COMMENT ON TABLE ingest.match_entries_unpacked IS
    'One row per WTT match per event, unpacked from '
    'ingest.match_entries_payloads_raw. Match ENTRIES: identifies matches, '
    'does not carry their details.';
COMMENT ON COLUMN ingest.match_entries_unpacked.match_id IS
    'Payload key "id", renamed to say what it is. Unique per payload and '
    'assumed global; the unpack is scoped to one event and refuses to reparent '
    'a match_id onto a different event_id.';
COMMENT ON COLUMN ingest.match_entries_unpacked.event_id IS
    'Payload key "eventId". A STRING upstream, cast to bigint here so it joins '
    'to events_unpacked.event_id without a cast on every query.';
COMMENT ON COLUMN ingest.match_entries_unpacked.document_code IS
    'Payload key "documentCode": a 42-char composite such as '
    'TTEMSINGLES-----------FNL-000100----------. Kept intact; splitting it into '
    'gender, draw and round is a business rule and belongs in dbt.';
COMMENT ON COLUMN ingest.match_entries_unpacked.sub_event_type IS
    'Payload key "subEventType": Men Singles, Women Singles, Men Doubles, '
    'Women Doubles, Mixed Doubles. Human readable label, not a code.';
COMMENT ON COLUMN ingest.match_entries_unpacked.full_results IS
    'Payload key "fullResults": result status as sent, "OFFICIAL" for every '
    'row seen so far. Not interpreted here.';
COMMENT ON COLUMN ingest.match_entries_unpacked.start_date_local IS
    'Payload key "startDateLocal" as timestamp WITHOUT time zone. The API sends '
    'no offset, so none is invented. The column name keeps that honesty.';
COMMENT ON COLUMN ingest.match_entries_unpacked.first_seen_at IS
    'When this match_id was first unpacked. Written once.';
COMMENT ON COLUMN ingest.match_entries_unpacked.last_seen_at IS
    'When this match was last present in its event payload. Advances on every '
    'unpack, so a value that stops moving means WTT dropped the match.';

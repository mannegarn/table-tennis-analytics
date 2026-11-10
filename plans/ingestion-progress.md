# Ingestion progress and decisions

Updated: 2026-10-27.

## Current state

- `make events-collect` reads `ingest.events_years_pending`, fetches each year in
  chunks, and upserts one row per year into `ingest.events_by_year_raw`.
- **102 years are landed: 1926-2027.** `source_final_count` and `received_count`
  agree for every year, and nine years are legitimately empty (1927, 1934,
  1940-1946). 2984 calendar rows in total, 2965 after unpacking.
- The first backfill took one run of 94 years in a few minutes, chunked at 50
  and concurrent at `max_concurrent_requests`. Every run after it is two
  requests: the current year and the next.
======= END"/>
- Every attempt is recorded in `ingest.ingest_runs` (migration 002), success or
  failure, with start/finish time, status, HTTP status, row count and error.
- `ingest.events_unpacked` is live: 736 rows, 736 distinct event ids, from the
  six landed years. The unpack statement runs automatically inside each landing
  transaction and by hand via `make events-unpack`.
- Per year, unpacked vs landed: 2021 86/99, 2022 91/94, 2023 101/101,
  2024 131/131, 2025 156/156, 2026 171/174. The 19 missing rows are duplicate
  events listed twice inside one year, never across years.
- `make events-unpack-preview YEARS=2023` runs the statement and rolls back, so
  the SQL can be exercised without touching the data.
- The 755 calendar rows collapse to **736 distinct EventIds**. Every duplicate is
  an exact repeat inside one year (2021: 13, 2022: 3, 2026: 3), and no EventId
  appears in two different years. The unpack statement de-duplicates with
  `DISTINCT ON`, because one `INSERT ... ON CONFLICT DO UPDATE` cannot affect
  the same row twice.
- The rotating `logs/tt_stats.log` (5 MB, three backups) records every `INFO`
  line; the console only shows `WARNING` and above. `LOG_CONSOLE_LEVEL` and
  `LOG_TO_FILE=false` change that.
- The runtime output is the database only. No payload or `.meta.json` files are
  written by a collect run.
- `data/raw/events/` still contains payload files from earlier runs. They are
  legacy, are not updated, and can be deleted later.
- The `ingest` and `analytics` schemas exist. Migrations are tracked in
  `ingest.schema_version` with a checksum per file.
- dbt writes to `analytics`. Ingestion tables are sources, not dbt models.
- The terminal uses one `tqdm` bar that rewrites a single line. Piped output,
  captured output and JSON logs draw no bar at all.

## Decisions

- **Years are sequential.** Six requests is a small fixed list. Concurrency and
  chunking belong to the match fan-out, where the request count grows with the
  data.
- **Default years are 2021 through the current year.** Change
  `DEFAULT_START_YEAR` or `default_years()` when the backfill policy changes.
- **Do not land failed fetches.** A failed response never overwrites a good
  payload. Failures go to `ingest_runs` instead.
- **No local payload writes.** The database row is the source of truth.
- **Logging is durable now (2026-10-27).** Console output plus a rotating file
  plus one `ingest_runs` row per attempt. The file covers diagnosis, the table
  covers history; neither is a collect flag.
- **Separate handler levels, no handler mutation (2026-10-30).** The progress
  bar owns one stdout line; the console handler logs `WARNING` and above to
  stderr, so routine `INFO` records cannot push the bar down a line. They still
  reach the file. There is no dynamic handler switching, so console `INFO` is
  opt-in via `LOG_CONSOLE_LEVEL` (or `tqdm.contrib.logging.logging_redirect_tqdm`).
- **Recording a run never breaks a collect.** If the insert fails, the failure is
  logged as `ingest_run_not_recorded` and the payload still lands.
- **Count names say who owns the number (2026-10-30).** `source_final_count` is
  WTT's `Count`, `source_original_count` is WTT's `Original_Count`, and
  `received_count` is what we parsed. `ingest_runs.row_count` was renamed to
  `received_count` too, so one vocabulary runs through the code. Migration 004 is
  a pure rename with column comments; no data was rewritten.
- **Intra-year duplicates are expected, not drift (2026-10-30).** WTT repeats
  whole event rows inside a single year's payload: 2021 lists the Tokyo Olympics
  three times. `unpack_events.sql` de-duplicates on `event_id`, so 2021 lands 86
  rows from 99 items, 2022 91 from 94 and 2026 171 from 174. The post-unpack
  assertion therefore compares `unpacked` against the number of **distinct**
  `EventId` values in the stored payload, never against `received_count`. A
  strict `unpacked == received_count` check would reject those three years.
- **Integration tests may never touch real years.** The unpack upserts on
  `event_id`, so loading the 2023 fixture under the test year reparented real
  events and cleanup then deleted them. Both landing tests now shift every
  EventId by 900,000,000 into a private range, and the unpack test asserts the
  real-year row count is unchanged after teardown.
- **`EventId` is the key, not the year.** It is unique across years, so the
  unpacked table is keyed on `event_id` and `source_year` is a fact rather than
  part of the identity.
- **Dates are `date`, never `timestamptz`.** The API sends `T00:00:00` with no
  offset, so a timestamp would invent a timezone that was never sent.
- **The unpacked table has structural constraints, not data-quality ones
  (revised 2026-11-10).** Blank names and absurd years are still rejected, because
  those point at a broken unpack. The inverted-date check was **removed** in
  migration 006: 1992 sends " World Cup  Ho Chi Minh City" with
  `StartDateTime 1992-12-10` and `EndDateTime 1991-12-13`, and rejecting that row
  aborted the year's transaction and silently discarded its other 15 events.
  Ordering dates is a domain rule, so it belongs in a dbt test where it flags one
  row instead of deleting a year (ADR 0003).
- **`events-inspect` was deleted on 2026-10-27.** It read JSON files, predated
  the database, and was only diagnostic. Re-add it later as a database report
  if the same questions return.
- **Keep the year range simple for now.** The pending view will decide which
  years are missing, current or stale. Do not add an `if` in the CLI first.
- **2027 and empty calendars are deferred.** The default will include 2027 next
  year. Decide then whether an empty payload is stored with `received_count=0`
  or treated as a failed fetch.
- **Year eligibility is derived too, with the same shape as events
  (2026-11-10).** Supersedes the earlier "calendar always re-fetches every
  year" note, which was written before the full-range backfill was wanted. See
  the year-level rules below: the current year and the next are refreshed every
  run, a missing past year is fetched once, and a past year that already landed
  is never fetched again.
- **A pre-2021 calendar backfill costs one wasted match request per event
  (2026-11-10).** Those events have no match data on this API, so each lands
  `[]`, is recorded, and is never asked again. Measure the pre-2021 event count
  after the calendar backfill, then decide whether the match queue clamps to
  2021 or simply absorbs the one-off cost.
- **One `ingest` schema, no per-domain subfolders (2026-11-10).** The domain is
  already in the table name (`events_*`, `match_entries_*`), and per-domain
  schemas would undo ADR 0007, split the grants and force one schema per dbt
  source. Folders only mirror the Python package, as in
  `src/tt_stats/events/land/sql/` and `src/tt_stats/matches/land/sql/`.
- **Migrations stay flat in `db/migrations/` (2026-11-10).** Order is the
  deployment contract, and `discover_migrations()` globs `*.sql` without
  recursing, so a migration filed in a subfolder would be silently skipped
  rather than applied.
- **One `ingest_runs` table for every domain (2026-11-10).** The row already
  carries `source` and `entity_key`, so the match fan-out records
  `wtt_getofficialresult` beside `wtt_eventcalendar` and no second table is
  needed. Forking it would turn "what failed today" into a UNION and duplicate
  the append-only invariant. If volume ever demands it, partition on `source`.
- **Match entries are a second pair, not a second schema (2026-11-10).**
  `match_entries_payloads_raw` is one row per event (the request grain) and
  `match_entries_unpacked` is one row per match, the same split as
  `events_by_year_raw` and `events_unpacked`. The name says entries, not
  details, because the payload carries `id`, `eventId`, `documentCode`,
  `subEventType`, `fullResults` and `startDateLocal` while `match_card`,
  `extended_info` and `messagePayload` arrive null until the deferred per-match
  details call fills them (ADR 0006).
- **Eligibility is again derived, never a stored flag (2026-11-10).** A boolean
  such as `to_scrape` is a cache of a query: it goes stale the moment an event
  starts or is rescheduled, it cannot say *why* an event is in the queue, and
  adding the one-day grace would become a backfill instead of one
  `CREATE OR REPLACE VIEW`. If the queue ever gets expensive, the answer is a
  materialised view refreshed per run, not per-row flags.
- **An empty match list is a success (2026-11-10).** Some 2021 events, and some
  future ones, legitimately return `[]`. Landing it with `received_count = 0`
  and a fresh `fetched_at` records the observation, and the queue then stops
  asking. Treating it as a failure would re-request the same events every run.
- **No `run_limited` helper (2026-11-10).** The client semaphore already caps
  in-flight requests at `max_concurrent_requests`, so a second limiter would
  bound nothing. One bound, one place.

## Year queue: eligibility rules (decided 2026-11-10)

The same derivation as the event-level rules, one level up. A read-only view,
`ingest.events_years_pending`, answers which years deserve a request: the
current year and the next are always worth asking about, a missing past year is
worth asking about once, and a past year that already landed is done.

`p` is `current_date`, `cy` is its year.

| Year state | Payload? | In queue? | `reason` |
| --- | --- | --- | --- |
| `year = cy` | either | yes | `current` |
| `year = cy + 1` | either | yes | `next` |
| `year < cy` | no | yes | `backfill` |
| `year < cy` | yes | no | - |
| `year > cy + 1` | either | no | - |

```sql
FROM generate_series(1900, cy + 1) AS y (year)
LEFT JOIN ingest.events_by_year_raw AS r USING (year)
WHERE y.year IN (cy, cy + 1)
   OR r.year IS NULL
ORDER BY (y.year = cy) DESC, (y.year = cy + 1) DESC, y.year DESC
```

- **Cost profile**: the first run is one request per missing year, roughly a
  century of them. Every run afterwards is two requests. That single expensive
  run is the whole point, and it is why chunking goes into the calendar
  collector now rather than later.
- **An empty year is a success** (see the decision above): `received_count = 0`,
  landed, remembered, never asked again. Without this, most pre-2000 years fail.
- **The floor is configuration, not SQL**: the view exposes candidates from
  1900 and the collector applies `WHERE year >= events_start_year`, so widening
  or narrowing the backfill is a setting.
- **Accepted cost**: a past year is frozen after its first landing, so an
  upstream cancellation or reschedule discovered later is never picked up. The
  escape hatch is `--years 2023`, which bypasses the view. The alternative was
  re-fetching 96 years on every run.
- **Blocker to clear first**: `events_by_year_raw.year` and
  `events_unpacked.source_year` both carry `CHECK (... BETWEEN 2000 AND 2100)`,
  so a 1930s year fails on insert. They must be widened in a new migration;
  editing 001 or 003 is refused by the checksum guard.

### Measured coverage (probe run, 2026-11-10)

`scripts/probe_events_by_year.py` swept 1926-2027: 102 requests, no failures.

| Range | Years | Calendar rows |
| --- | --- | --- |
| 1926-1999 | 74 | 511 |
| 2000-2020 | 21 | 1602 |
| 2021-2027 | 7 | 871 |
| **1926-2027** | **102** | **2984** |

- **The first year with data is 1926**, so `events_start_year` defaults to 1926
  rather than 1900. Years 1927, 1934 and 1940-1946 return no rows at all.
- **Landed for real on 2026-11-10**: 102 years in `events_by_year_raw` (1926-2027),
  2984 calendar rows, 9 empty years, 2965 rows in `events_unpacked`. A second run
  of `make events-collect` correctly finds nothing new except the current and
  next year.
- **An empty year is not `[]`.** It is the normal envelope with
  `rows: []`, `Count: 0`, `Original_Count: 0`, 228 bytes. The collector's shape
  check already accepts that envelope; what fails today is `extract_rows()`
  rejecting an empty `rows` list.
- **2027 is already published** with 116 events, which is the evidence for
  refreshing `current year + 1` rather than only the current year.
- **The archive grew from 736 events to 2965**, so `events_unpacked` and every
  downstream model now scale by about 4x.
- **2111 of those events predate 2021** and are expected to return no match data
  at all. On the first match-entries backfill that is 2111 requests spent to
  record `[]` once. Decide whether the match queue clamps to 2021 once that
  number is confirmed against real responses.
- **The calendar endpoint stalls.** Two of three sampled years exceeded a 40s
  timeout before the probe retried. The collector's six retries with backoff
  cover it, but a full sweep should be expected to take minutes, not seconds.

## Match-entries queue: eligibility rules (decided 2026-11-10)

The queue is a read-only view, `ingest.match_entries_pending`. It joins
`events_unpacked` to `match_entries_payloads_raw` and answers one question:
which events deserve a request right now.

`p` is `current_date`; `w` is `end_date + 1`, the **one-day grace**, so an event
ending yesterday still counts as live. The same boundary is used by the stale
test, so the two rules cannot drift apart.

| Event state | Payload? | In queue? | `reason` |
| --- | --- | --- | --- |
| `start_date > p` | either | no | - |
| `start_date <= p` and `end_date + 1 >= p` | no | yes | `live` |
| `start_date <= p` and `end_date + 1 >= p` | yes | yes | `live` |
| finished, `end_date + 1 < p` | no | yes | `new` |
| finished | yes, `fetched_at::date <= end_date + 1` | yes | `stale` |
| finished | yes, `fetched_at::date > end_date + 1` | no | - |

```sql
WHERE e.start_date <= p
  AND (
        e.end_date + 1 >= p                      -- live, plus the grace
     OR r.event_id IS NULL                        -- finished, never fetched
     OR r.fetched_at::date <= e.end_date + 1       -- finished, snapshot too early
  )
```

- **The stale rule cannot loop.** The next fetch must land after the window
  closed, so the clause stops matching. One extra request, maximum.
- **Ordering is priority, not filtering** (ADR 0006): live first, then finished
  by most recent end date, `ORDER BY (e.end_date + 1 >= p) DESC, e.end_date DESC`.
  So `--limit 5` is a live smoke test rather than a random 2021 slice.
- **`reason` exists so a 736-event run is debuggable** from the log line and the
  bar postfix alone.
- **Grace changes are migrations.** The integer lives in the view; tuning it is
  a new migration with `CREATE OR REPLACE VIEW`, which is cheaper and more
  auditable than a settings table for one value.
- **Timezone caveat accepted.** `current_date` follows the database session
  timezone while the API sends venue-local datetimes with no offset, so a boundary
  can be a day out. Fetched early is caught by the stale rule; fetched late loses
  nothing.

### Match data only exists from late 2021 (probed 2026-11-10)

`scripts/probe_match_entries.py` probed 18 real event ids across the archive:

| Sample | Result |
| --- | --- |
| 1930, 1967, 1992, 2010, 2020 (5 events) | `[]`, 2 bytes, every time |
| 2021, event ids below ~2500 (9 events) | `[]`, 2 bytes, every time |
| 2021, event ids 2503-2519 (4 events) | 89-368 rows |
| 2022 (3 events) | 89-259 rows |
| 2025 event 3098 (control) | 306 rows |

- **An empty answer is `[]`, not an envelope.** Two bytes of JSON. So the unpack
  must accept a zero-row payload, and `received_count = 0` is a success.
- **No year floor: every event is asked once (decided 2026-11-10).** A floor of
  2021 was considered and rejected. It would save ~2111 requests, but it leaves
  "does WTT have anything for 1934?" answerable only by re-running a probe,
  whereas storing the empty answer makes the database the evidence:
  `SELECT source_year, count(*) FROM events_unpacked JOIN
  match_entries_payloads_raw USING (event_id) WHERE received_count = 0`.
- **Empty answers are recorded, not repeated.** A landed `[]` writes a raw row
  with `received_count = 0`, so row existence marks the event as asked and the
  queue never returns to it. Accepted cost: if WTT later publishes old match
  data, we will not notice without a manual re-run.
- **The first run is therefore ~2983 requests**, not ~872. At 20 in flight that
  is minutes, and it happens once.
- **Upstream is inconsistent about the match-id key (found 2026-11-10).**
  Events 3098, 2519 and the 2022 samples send `id`; events 3257 and 3346 send
  `iD`. Same integer, different casing. The collector accepts either through
  `MATCH_ID_KEYS`, and the unpack must coalesce
  `payload ->> 'id'` with `payload ->> 'iD'` into one `match_id`. Assuming the
  lower-case key would have silently rejected two live events (35 and 206 rows)
  on the first smoke run.

## Match-entries collector (built 2026-11-10)

Modules, in build order:

1. `wtt_routes.get_match_entries_route(event_id)` returning `method: GET`,
   `params: {EventId, DocumentCode: TTE}` and the shared headers.
2. `WttClient.get_json`, `_get_once` and `fetch_match_entries(event_id)`,
   reusing the semaphore, retry policy, jitter and `Retry-After` handling that
   the POST path already has.
3. `matches/land/sql/unpack_match_entries.sql` plus its loader, **not yet
   written**: scoped to one event, `DISTINCT ON (match_id)` over
   `coalesce(payload ->> 'id', payload ->> 'iD')`, upsert on `match_id`, with a
   conflict guard so a `match_id` seen under a different `event_id` fails
   instead of reparenting.
4. `matches/land/match_entries.land_match_entries()`: payload upsert today.
   The unpack and its completeness assertion join this same transaction when
   the SQL exists, mirroring `land_events_by_year`.
5. `matches/collect/match_entries.py`: queue read, chunk loop, per-event land,
   `ingest_runs` row per attempt, progress bar, CLI.

Loop shape:

```
queue = SELECT event_id FROM ingest.match_entries_pending        -- ordered
for chunk in slices(queue, settings.chunk_size):                 -- 200
    summaries = gather(fetch_one(event_id) for event_id in chunk) -- semaphore caps I/O
    each summary: land in its own transaction, then record the run
    bar.update(1) per settled event, postfix = last reason + status
exit 1 if any summary carries an error
```

- **Chunking is scheduling and commit granularity, not concurrency.** The
  semaphore already caps in-flight requests; chunking bounds how much unlanded
  work one crash can lose and makes failure reporting per chunk. Both collectors
  now share `core/batching.chunks()`.
- **One failure never aborts a chunk.** Client errors (4xx) fail fast rather
  than retry, per event, and the run continues.
- **Landing is synchronous psycopg inside async code.** A few milliseconds per
  event; wrap in `asyncio.to_thread` only if the bar visibly stutters.
- **One `ingest_runs` row per event**, `source = wtt_getofficialresult`,
  `entity_key = event_id`.

### Measured collection (2026-11-10)

The first full run took eight batches of 400 and about seven minutes.

| Measure | Value |
| --- | --- |
| Events fetched | 2838 |
| Empty answers (`received_count = 0`) | 2307 |
| Events with matches | 531 |
| Match rows stored in payloads | 200,879 |
| Payload size | 2.7 MB |
| Left pending afterwards | 3 (the live events, by design) |

- **The no-floor decision paid for itself twice.** Pre-2021 events return empty,
  as predicted, but not universally: a 2012 event returned 1 match and a 2019
  event returned 19. A 2021 floor would have silently dropped those 20 rows.
- **The payloads are tiny** (2.7 MB for 2838 events), so the raw archive is
  cheap and the storage conversation only concerns the deferred match logs.
- **The queue empties to the live events only**, which is exactly the steady
  state: two calendar requests plus a handful of live match-list requests per
  run.
- **Two failed attempts remain in `ingest_runs`** from the smoke run before the
  `id`/`iD` fix. They are history, not current state, and both events landed
  successfully afterwards.

## Match-entries stage checkpoint (2026-11-10)

Verified:

- Migration 004 creates `match_entries_payloads_raw` (one row per event) and
  `match_entries_unpacked` (one row per match, still empty: the unpack is next).
- Migration 005 widens the year checks to 1900 and adds `events_years_pending`,
  migration 006 drops the inverted-date check, migration 007 adds
  `match_entries_pending` with the six-state rules and no year floor.
- `get_match_entries_route` and `fetch_match_entries` share the POST path's
  semaphore, retry policy, jitter and `Retry-After` handling.
- `tt-matches-collect` / `make matches-collect` read the view, fetch in chunks,
  land one event per transaction, and write one `ingest_runs` row per attempt.
- Full run: 2838 events, 2307 empty answers, 531 with matches, 200,879 match
  rows, 2.7 MB. The queue then holds only the live events.
- Both collectors print a run summary and log one line per chunk with that
  chunk's rate; per-item records are `DEBUG`, failures stay at `ERROR`.
- `--limit` and `--chunk-size` are validated by argparse before any Settings or
  database work, so a typo exits 2 without needing a password.
- `ruff check`, `ruff format --check`, `mypy` and `pytest` are clean
  (158 passed, 1 skipped).

Known open items:

- **The unpack is not written.** `match_entries_unpacked` is empty by design, and
  `land_match_entries` deliberately does raw-only until it exists.
- **Crash gap:** only settled attempts are recorded, so a kill mid-run leaves no
  `started` row. Acceptable while the queue is re-derivable from row existence.
- **`make db-test-check` does not count the match tables yet.**

### Match-entries payload contract (measured)

This is the only record of the response shape, kept here rather than in a
separate document that nothing validates.

| Key | Type | Notes |
| --- | --- | --- |
| `id` **or** `iD` | int | Casing varies upstream: event 3098 and the 2022 events send `id`, events 3257 and 3346 send `iD`. The unpack must coalesce both into `match_id`. |
| `eventId` | str | A string, although the calendar sends an int. Cast to bigint to join `events_unpacked`. |
| `documentCode` | str | 42 chars: gender + draw + round + match number. Kept whole; splitting it is a dbt rule. |
| `subEventType` | str | Men Singles, Women Singles, Men/Women/Mixed Doubles. |
| `fullResults` | str | "OFFICIAL" in every row seen so far. |
| `startDateLocal` | str | `YYYY-MM-DDTHH:MM:SS`, no offset, so it lands as `timestamp without time zone`. |
| `match_card`, `extended_info`, `messagePayload` | null | Empty in this call; they belong to the deferred per-match details scrape (ADR 0006). |

The body is a bare array, not a `rows` envelope, and an event with no matched
draw answers `[]`, two bytes of it.

## Next steps

1. `unpack_match_entries.sql` plus its loader: `coalesce(payload ->> 'id',
   payload ->> 'iD')`, scoped to one event, upsert on `match_id`, with a conflict
   guard so a match id seen under a different event fails instead of reparenting.
2. Run it inside the landing transaction and assert the unpacked count equals
   the distinct ids in the payload, allowing zero rows for an empty answer.
3. `tt-matches-unpack` and `make matches-unpack` to catch up the 2838 events
   already landed.
4. Integration tests: idempotency, an empty payload writing zero rows, and the
   reparenting guard.
5. Add the match tables to `make db-test-check`.
6. `dbt/models/events/sources.yml` over the `_unpacked` tables with freshness on
   `fetched_at`, then the marts under `analytics`.

## How to inspect now

Use DBeaver or `make db-psql`.

```sql
SELECT entity_key, status, http_status, received_count, started_at, error
FROM ingest.ingest_runs
ORDER BY run_id DESC
LIMIT 20;
```

```sh
tail -f logs/tt_stats.log
```

```sql
SELECT event_id, event_name, start_date, end_date, source_year, first_seen_at, last_seen_at
FROM ingest.events_unpacked
ORDER BY start_date DESC
LIMIT 20;

-- Events whose last_seen_at stopped advancing: dropped upstream, kept for history.
SELECT event_id, event_name, source_year, last_seen_at
FROM ingest.events_unpacked
ORDER BY last_seen_at
LIMIT 20;

SELECT year, http_status, source_final_count, received_count, fetched_at
FROM ingest.events_by_year_raw
ORDER BY year;

SELECT year, payload->0->'rows'->0 AS first_event
FROM ingest.events_by_year_raw
WHERE year = 2023;

-- The match-entries queue: what a collect run would request right now.
SELECT event_id, event_name, end_date, reason, previous_received_count
FROM ingest.match_entries_pending
ORDER BY end_date DESC
LIMIT 20;

-- Which events answered with no matches at all, by year.
SELECT e.source_year, count(*) AS events_without_matches
FROM ingest.events_unpacked AS e
JOIN ingest.match_entries_payloads_raw AS r USING (event_id)
WHERE r.received_count = 0
GROUP BY 1
ORDER BY 1;

SELECT event_id, received_count, pg_size_pretty(pg_column_size(payload)::bigint) AS size
FROM ingest.match_entries_payloads_raw
ORDER BY received_count DESC
LIMIT 10;
```

The old `make events-inspect` target no longer exists.

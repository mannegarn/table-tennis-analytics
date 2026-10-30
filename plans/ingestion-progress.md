# Ingestion progress and decisions

Updated: 2026-10-27.

## Current state

- `make events-scrape` fetches the WTT event calendar for 2021 through the
  current year, validates it, and upserts one row per year into
  `ingest.events_by_year_raw`.
- Six rows are landed: 2021–2026. `source_final_count` and `received_count` agree
  for every year. Event counts: 2021 99, 2022 94, 2023 101, 2024 131, 2025 156, 2026 174
  (755 events, which is the size of the coming match-list fan-out).
- Measured payload size: 72 kB in total for all six years, 9–16 kB per year.
  The calendar payloads are negligible; all real volume comes from the match and
  match-detail fan-out, so size it there.
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
  written by a scrape.
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
  covers history; neither is a scrape flag.
- **Separate handler levels, no handler mutation (2026-10-30).** The progress
  bar owns one stdout line; the console handler logs `WARNING` and above to
  stderr, so routine `INFO` records cannot push the bar down a line. They still
  reach the file. There is no dynamic handler switching, so console `INFO` is
  opt-in via `LOG_CONSOLE_LEVEL` (or `tqdm.contrib.logging.logging_redirect_tqdm`).
- **Recording a run never breaks a scrape.** If the insert fails, the failure is
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
- **The unpacked table has constraints, not filters.** Blank names, inverted date
  ranges and years outside 2000–2100 are rejected at write time; nothing is
  dropped or corrected. All 755 real rows satisfy them.
- **`events-inspect` was deleted on 2026-10-27.** It read JSON files, predated
  the database, and was only diagnostic. Re-add it later as a database report
  if the same questions return.
- **Keep the year range simple for now.** The pending view will decide which
  years are missing, current or stale. Do not add an `if` in the CLI first.
- **2027 and empty calendars are deferred.** The default will include 2027 next
  year. Decide then whether an empty payload is stored with `received_count=0`
  or treated as a failed fetch.

## Checkpoint before the match fan-out

Verified on 2026-10-27:

- Migrations 001–003 apply cleanly, and a second `make db-migrate` is a no-op.
- `make events-scrape` lands six years in a few seconds and writes one
  `ingest_runs` row per attempt.
- Console, rotating file and run table agree on the same six years.
- `ingest.events_unpacked` shape, constraints and upsert are covered by
  integration tests. The table is empty by design.
- `ruff check`, `ruff format --check`, `mypy` and `pytest` are clean.

Still missing before per-event scraping can start:

- **No matches route.** `wtt_routes.py` only knows the calendar endpoint; there
  is no `get_event_matches_route` yet. The old repo's version is the reference.
- **No unpack statement**, so nothing turns landed events into a work queue.
- **No `ingest.event_matches_raw`** table and no matches collect/land modules.
- **No pending view**, so "which events still need fetching" has no answer.
- **Crash gap:** only settled attempts are recorded, so a run killed mid-flight
  leaves no `started` row. Acceptable while the queue is re-derivable from row
  existence; revisit if a half-finished run ever becomes ambiguous.

## Next steps

1. Write the statement in `src/tt_stats/events/land/sql/unpack_events.sql`:
   `DISTINCT ON (event_id)` over `payload -> 0 -> 'rows'`, cast to the six
   columns, upsert on `event_id`. The file lists the target shape, the mapping
   and the gotchas. No Python changes are needed when it lands.
2. Run `make events-unpack` and expect 736 rows, not 755. The integration tests
   stop skipping at the same time and verify landing, idempotency and the
   distinct-event total.
3. Create `ingest.event_matches_raw` and land match lists for every event.
5. Add the match fan-out with a shared `run_limited` helper and chunking.
6. Add the pending view over raw, unpacked and matches, deriving eligibility.
7. Add `dbt/models/events/sources.yml` declaring `ingest.events_by_year_raw`
   with freshness on `fetched_at`, then move cleaning and business rules into
   dbt under `analytics`.

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
```

The old `make events-inspect` target no longer exists.

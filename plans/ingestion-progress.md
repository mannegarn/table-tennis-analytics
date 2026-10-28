# Ingestion progress and decisions

Updated: 2026-10-27.

## Current state

- `make events-scrape` fetches the WTT event calendar for 2021 through the
  current year, validates it, and upserts one row per year into
  `ingest.events_by_year_raw`.
- Six rows are landed: 2021–2026. `api_count` and `row_count` agree for every
  year. Event counts: 2021 99, 2022 94, 2023 101, 2024 131, 2025 156, 2026 174
  (755 events, which is the size of the coming match-list fan-out).
- Measured payload size: 72 kB in total for all six years, 9–16 kB per year.
  The calendar payloads are negligible; all real volume comes from the match and
  match-detail fan-out, so size it there.
- Every attempt is recorded in `ingest.ingest_runs` (migration 002), success or
  failure, with start/finish time, status, HTTP status, row count and error.
- `ingest.events_unpacked` exists (migration 003) and is empty. Everything except
  the SQL is wired: migration, loader, landing-transaction call, `make
  events-unpack`, console script, unit guardrails and integration tests.
- The statement lives at `src/tt_stats/events/land/sql/unpack_events.sql` and is
  deliberately comments only. While it stays that way, landing logs
  `unpack_not_implemented` and continues, and `make events-unpack` exits 1. The
  unit guardrails and the integration tests skip themselves until it exists.
- The 755 calendar rows collapse to **736 distinct EventIds**. Every duplicate is
  an exact repeat inside one year, and no EventId appears in two different years.
  The unpack statement must de-duplicate before inserting, because one
  `INSERT ... ON CONFLICT DO UPDATE` cannot affect the same row twice.
- Console output is mirrored to `logs/tt_stats.log`, rotating at 5 MB with three
  backups. `LOG_TO_FILE=false` disables it.
- The runtime output is the database only. No payload or `.meta.json` files are
  written by a scrape.
- `data/raw/events/` still contains payload files from earlier runs. They are
  legacy, are not updated, and can be deleted later.
- The `ingest` and `analytics` schemas exist. Migrations are tracked in
  `ingest.schema_version` with a checksum per file.
- dbt writes to `analytics`. Ingestion tables are sources, not dbt models.
- The terminal uses one compact `tqdm` bar with start and finish blocks.
  Piped output and JSON logs keep the per-year records instead.

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
- **The progress bar owns the console.** `progress_owns_console()` filters
  sub-error records out of the console handler while `tqdm` is drawing, so
  land and unpack lines cannot shred the bar. The file handler is untouched and
  errors still print. Non-interactive runs keep the full console output.
- **Recording a run never breaks a scrape.** If the insert fails, the failure is
  logged as `ingest_run_not_recorded` and the payload still lands.
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
  year. Decide then whether an empty payload is stored with `row_count=0` or
  treated as a failed fetch.

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
SELECT entity_key, status, http_status, row_count, started_at, error
FROM ingest.ingest_runs
ORDER BY run_id DESC
LIMIT 20;
```

```sh
tail -f logs/tt_stats.log
```

```sql
SELECT event_id, event_name, start_date, end_date, source_year, refreshed_at
FROM ingest.events_unpacked
ORDER BY start_date DESC
LIMIT 20;

SELECT year, http_status, api_count, row_count, fetched_at
FROM ingest.events_by_year_raw
ORDER BY year;

SELECT year, payload->0->'rows'->0 AS first_event
FROM ingest.events_by_year_raw
WHERE year = 2023;
```

The old `make events-inspect` target no longer exists.

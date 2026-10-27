# Ingestion progress and decisions

Updated: 2026-10-27.

## Current state

- `make events-scrape` fetches the WTT event calendar for 2021 through the
  current year, validates it, and upserts one row per year into
  `ingest.events_by_year_raw`.
- Six rows are landed: 2021–2026. `api_count` and `row_count` agree for every
  year.
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
  payload. Failures will go to `ingest_runs`.
- **No local payload writes.** The database row is the source of truth.
- **`events-inspect` was deleted on 2026-10-27.** It read JSON files, predated
  the database, and was only diagnostic. Re-add it later as a database report
  if the same questions return.
- **No durable logging yet.** Logging goes to the console; there is no file
  handler and no `ingest_runs`. The raw table is the durable ingestion record.
- **Keep the year range simple for now.** The pending view will decide which
  years are missing, current or stale. Do not add an `if` in the CLI first.
- **2027 and empty calendars are deferred.** The default will include 2027 next
  year. Decide then whether an empty payload is stored with `row_count=0` or
  treated as a failed fetch.

## Next steps

1. Add `dbt/models/events/sources.yml` declaring
   `ingest.events_by_year_raw` with freshness on `fetched_at`.
2. Add a file log handler and `ingest_runs` for run history and failed fetches.
3. Create `ingest.events_unpacked` with only the fields the scrape queue needs.
4. Add the pending view and make the queue read from the raw and unpacked
   tables.
5. Add the match fan-out with a shared `run_limited` helper and chunking.
6. Move cleaning and business rules into dbt under `analytics`.

## How to inspect now

Use DBeaver or `make db-psql`.

```sql
SELECT year, http_status, api_count, row_count, fetched_at
FROM ingest.events_by_year_raw
ORDER BY year;

SELECT year, payload->0->'rows'->0 AS first_event
FROM ingest.events_by_year_raw
WHERE year = 2023;
```

The old `make events-inspect` target no longer exists.

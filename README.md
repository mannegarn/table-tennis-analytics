# Table Tennis Analytics Platform

End-to-end data platform for international table tennis: collect from the
source, land raw payloads in PostgreSQL, transform with dbt, and analyse with
Polars.

> Status: ongoing. Nothing is deployed yet.

## Stack

| Layer | Tool |
| --- | --- |
| Warehouse | PostgreSQL 16 (Docker) |
| Transforms | dbt (dbt-postgres) |
| Ingestion | Python 3.13, httpx, psycopg 3, uv |
| Analytics | Polars, Jupyter |
| Dashboard | Streamlit |
| Orchestration | Plain Python now, Apache Airflow later |
| Quality | ruff, mypy, pytest, pre-commit |

## Prerequisites

- **uv** for Python and dependency management.
- **Docker** reachable from the shell.

### Windows and WSL note

Docker must be reachable from inside this WSL distro. In Docker Desktop go to
**Settings -> Resources -> WSL Integration** and enable it for this distro.
Confirm with `docker --version`. If it prints `docker: command not found`, the
integration is switched off.

## Setup

```sh
make setup       # creates .env from .env.example, then runs uv sync
make db-up       # starts PostgreSQL in the background
make db-migrate  # creates the ingest and analytics schemas and tables
make db-check
```

The same steps done manually:

```sh
cp .env.example .env
uv sync
docker compose up -d
uv run tt-db-migrate
uv run tt-db-check
```

## Verification

- `uv run tt-db-check` prints `Connected to PostgreSQL: PostgreSQL 16.4 ...`
- `make db-migrate` applies `db/migrations` and records versions in
  `ingest.schema_version`. Re-running it is a no-op.
- `make db-psql` opens a shell; run `\dt ingest.*` to list ingestion tables.
- `SELECT * FROM ingest.ingest_runs ORDER BY run_id DESC LIMIT 10;` shows the
  most recent attempts, including failures that never landed a payload.
- `ls logs/` shows the run log written alongside the console output.
- `make dbt-debug` ends with `All checks passed!`
- `make test` runs the suite; the integration test skips automatically when the database is down.
- `make test-integration` runs the database integration tests explicitly.
- Integration tests run against `tt_stats_test`, not the warehouse, so collected
  data is never touched. `make db-test-check` shows its applied migrations and row
  counts; `make db-test-psql` opens a shell on it.

## Events collection

The events calendar is fetched and upserted into
`ingest.events_by_year_raw`. Each year is one row and one transaction. No
payload files are written. A landing failure marks that year as failed and
makes the command exit non-zero.

```sh
make events-collect                 # whatever the pending view says
make events-collect YEARS="2023"    # one year, bypassing the view
make events-collect LIMIT=5         # first five years in queue order
```

Which years get fetched is derived, never stored (`ingest.events_years_pending`):

| Year | Payload already landed? | Fetched? | `reason` |
| --- | --- | --- | --- |
| current year | either | yes, every run | `current` |
| next year | either | yes, every run | `next` |
| any other year | no | yes, once | `backfill` |
| any other year | yes | no | - |

So the first run is the whole history and every run after it is two requests.
Years are fetched in chunks of `CHUNK_SIZE` (default 50), concurrently within a
chunk and landed one transaction at a time, so a crash cannot lose more than the
chunk in flight. A year with no tournaments is a success: it lands with
`received_count = 0` and is never asked for again.

Measured coverage on 2026-11-10: **102 years, 1926-2027**, 2984 calendar rows,
nine empty years (1927, 1934, 1940-1946), 2965 unpacked events.

Each row stores the JSONB payload plus the fetch metadata: HTTP status, SHA-256,
`fetched_at`, and three counts that are easy to confuse:

| Column | Whose | Means |
| --- | --- | --- |
| `received_count` | ours | Rows we actually parsed from `payload -> 0 -> 'rows'` |
| `source_final_count` | WTT's `Count` | What the API says it returned; must match `received_count` |
| `source_original_count` | WTT's `Original_Count` | Their total before their own internal step; informational only |

A year fails instead of landing when `source_final_count != received_count`, so a
truncated or reshaped response cannot pass silently.

Every attempt, successful or failed, is also written to `ingest.ingest_runs`
with its start and finish time, status, HTTP status, row count and error. That
is the durable history: a failed year leaves a row, so nothing depends on
scrolling back through a terminal.

`ingest.events_unpacked` holds one row per event per calendar: 2965 rows from
2984 calendar rows. The difference is duplicates: 2021, 2022 and 2026 each list
some events more than once, and the unpack de-duplicates them. The key is
`(source_year, event_id)`, so a tournament listed in two years'
calendars keeps a row in both instead of the later landing overwriting the
earlier one. `first_seen_at` is written once and `last_seen_at` advances on
every unpack, which is how an event WTT dropped is spotted without deleting
anything. The statement lives in `src/tt_stats/events/land/sql/unpack_events.sql`
and runs inside the same transaction as the raw insert, so a year is never
half-landed.

The same statement can be run by hand:

```sh
make events-unpack-preview YEARS="2023"   # run it, print counts, roll back
make events-unpack YEARS="2023"           # commit one year
make events-unpack                        # every year in the raw table
```

A terminal uses one `tqdm` bar that rewrites a single line, so a long collect
run does not print one line per item. The bar carries elapsed time, estimated
time remaining and a live rate, because "how long left" is the question a long
scrape actually raises. The console handler only prints `WARNING` and above, so
routine records cannot push the bar down a line; every record still goes to
`logs/tt_stats.log` and to `ingest.ingest_runs`. Piped, captured and JSON runs
draw no bar and keep the per-item log records.

```text
Collecting match entries (50/chunk, 20 workers): 1200/2838 [##########] [02:14<03:03, 8.94event/s] live event=3257 | matches=35 | ok
```

The title states the chunk size and the worker count, so a plateau in the rate is
explained: at most that many requests are ever in flight.

Each run ends with a summary line, printed even when there is no terminal, and
recorded in the log with `elapsed_seconds` and `rate_per_second`:

```text
2,840 events in 2m 05s (22.6/s), 57 chunks: 200,879 matches found, 2,309 events with no matches, 0 failed
102 years in 5.2s (19.6/s), 3 chunks: 2,984 events found, 9 years with no events, 0 failed
```

Log volume is per chunk, not per item. `match_entries_chunk` and `events_chunk`
record `chunk=i/n`, the item count for that chunk, matches found, empties,
failures and the chunk's own rate, so a 2840-event run writes about 57 lines
rather than 2840. Per-item records are `DEBUG`; failures stay at `ERROR`, one
per item, because there are few and each one matters.

Console output is mirrored to a rotating file, so no `tee` is needed:

```text
logs/tt_stats.log        # 5 MB per file, 3 backups
```

Set `LOG_TO_FILE=false` to disable it, or `LOGS_DIR` to move it. `logs/` is
git-ignored.

The 2023 payload is committed at `tests/fixtures/events_2023.json` for the
offline contract test. Run `make db-migrate` first; the command writes only to
the database. `data/` remains git-ignored for legacy captures and scratch files.

## Match entries collection

One request per event returns that tournament's whole match list, so the
request grain is the event and each response is stored as one row in
`ingest.match_entries_payloads_raw`.

```sh
make matches-collect                    # whatever the pending view says
make matches-collect LIMIT=5            # first five events in queue order
make matches-collect EVENT_IDS="3098"   # one event, bypassing the view
```

Which events are requested is derived in `ingest.match_entries_pending`, never
stored:

| Event state | Fetched? | `reason` |
| --- | --- | --- |
| has not started yet | no | - |
| in progress, or ended today or yesterday | yes, every run | `live` |
| finished, never fetched | yes, once | `new` |
| finished, last fetch on or before its final day | yes, once more | `stale` |
| finished, fetched after its final day | no | - |

Events are fetched in chunks of `CHUNK_SIZE` (default 50), concurrently within a
chunk, and landed one transaction at a time. An empty answer is `[]` and is
stored with `received_count = 0`, so "WTT has no matches for this event" is a
recorded fact rather than a question asked every run.

Two payload quirks are handled explicitly: the body is a bare JSON array rather
than a `rows` envelope, and the match id arrives as `id` for some events and
`iD` for others (the unpack will coalesce them).

Measured on 2026-11-10: **2838 events fetched, 2307 empty answers, 531 events
with matches, 200,879 match rows, 2.7 MB of payload.** No match data exists
before 2012, and only sample traces in 2012 and 2019; the archive becomes
substantial from 2021.

## Command surface

| Command | Purpose |
| --- | --- |
| `make help` | List every command |
| `make setup` | Create `.env` if missing and install dependencies |
| `make db-up` | Start PostgreSQL |
| `make db-down` | Stop PostgreSQL, keeping data |
| `make db-reset` | Destroy the volume and recreate the database |
| `make db-logs` | Tail PostgreSQL logs |
| `make db-psql` | Open a `psql` shell |
| `make db-test-psql` | Open a `psql` shell against `tt_stats_test` |
| `make db-check` | Verify the Python to PostgreSQL connection |
| `make db-test-check` | Verify `tt_stats_test` exists, is migrated and count its fixture rows |
| `make db-migrate` | Apply versioned SQL migrations |
| `make events-collect` | Collect event calendars and land them in `ingest.events_by_year_raw` |
| `make events-unpack` | Unpack landed calendars into `ingest.events_unpacked` |
| `make matches-collect` | Collect match lists and land them in `ingest.match_entries_payloads_raw` |
| `make events-unpack-preview` | Run the unpack for one year, print counts, roll back |
| `make test` | Run the test suite |
| `make test-integration` | Run only integration tests |
| `make dbt-debug` | Verify dbt can reach the warehouse |
| `make lint` | Lint with ruff |
| `make format` | Format with ruff |
| `make typecheck` | Type check with mypy |
| `make check` | Lint, format check and type check (same tools as pre-commit) |
| `make hooks-install` | Install git hooks and run them once (requires `git init`) |
| `make hooks-run` | Run every hook against every file |

## Code quality

The same checks run locally and in pre-commit:

- **ruff** for linting and formatting.
- **mypy** for static type checking, including the `pydantic.mypy` plugin.
- **pytest** for tests.
- **pre-commit** for file hygiene: trailing whitespace, end-of-file newlines,
  line endings, large files, private keys, merge conflicts, and YAML/TOML validity.

Hooks fire on commit (ruff check, ruff format, mypy) and on push (pytest), so a
push cannot be made with failing tests.

```sh
make check          # ruff check, ruff format --check, mypy
make hooks-install  # install git hooks, then run them once
make hooks-run      # run every hook against every file
```

Note: `make hooks-install` needs this folder to be a git repository, so run
`git init` first. Until then, use `make check` and `make test` directly.

## Configuration

There is a single source of truth for connection details: `.env`, which is
git-ignored. `.env.example` is committed and mirrors it.

- The Python app reads it through `pydantic-settings` in `src/tt_stats/core/config.py`.
- dbt reads the same variables through `env_var()` in `dbt/profiles.yml`.
- `docker compose` reads the same file for the container.

| Variable | Purpose |
| --- | --- |
| `POSTGRES_USER` | Application user |
| `POSTGRES_PASSWORD` | Application password (required; no default is shipped) |
| `POSTGRES_DB` | Database name |
| `POSTGRES_HOST` | Host (`localhost` on the host machine) |
| `POSTGRES_PORT` | Published port |
| `MIGRATIONS_DIR` | Migration directory; defaults to `db/migrations` in the repo |
| `DATA_DIR` | Local scratch directory; defaults to `data/` in the repo |
| `LOG_LEVEL` | Level of the rotating log file: `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` |
| `LOG_CONSOLE_LEVEL` | Level printed to the terminal; defaults to `WARNING` so the progress bar stays clean |
| `LOG_JSON` | `true` emits one JSON object per line instead of console text |
| `LOG_TO_FILE` | `true` also writes to the rotating log file; defaults to `true` |
| `LOGS_DIR` | Log directory; defaults to `logs/` in the repo |
| `LOG_FILE_MAX_BYTES` | Size at which the log rotates; defaults to 5 MB |
| `LOG_FILE_BACKUP_COUNT` | Rotated files kept; defaults to 3 |

`DATABASE_URL` is optional and, when set, overrides the individual parts.

Paths default to the repository layout, derived from the location of
`src/tt_stats/core/config.py`. Set `MIGRATIONS_DIR`, `DATA_DIR` or `LOGS_DIR`
when the process runs outside a checkout, for example from a wheel or a
container.

## Project layout

Domain first, then data level.

```
src/tt_stats/core/        shared config, database, logging, run history, clients
src/tt_stats/events/      collect and land events
src/tt_stats/matches/     collect and land match entries and details
src/tt_stats/players/     collect and land player details and playstyle
db/init/                  one-time SQL on first database start
db/migrations/            versioned tables, unpack SQL, views, control tables
dbt/models/<domain>/      marts models per domain, reading _unpacked tables
docs/adr/                 architecture decision records
logs/                     rotating run log, git-ignored
notebooks/                Polars analytics
app/                      Streamlit dashboard
airflow/dags/             orchestration (later)
```

## Data layers

Raw payloads land in the `ingest` schema as JSONB. The same transaction then
unpacks the payload into `ingest.events_unpacked`, one row per event, using
`src/tt_stats/events/land/sql/unpack_events.sql`. dbt reads ingestion tables as
sources and writes every model to `analytics`.

| Level | Schema | Naming | Example | Owner |
| --- | --- | --- | --- | --- |
| Raw | `ingest` | `<entity>_raw` | `ingest.events_by_year_raw` | ingestion |
| Unpacked | `ingest` | `<entity>_unpacked` | `ingest.events_unpacked` | ingestion, in SQL |
| Marts | `analytics` | `<entity>_marts` | `analytics.events_marts` | dbt |
| Control | `ingest` | `ingest_*` | `ingest.ingest_runs` | ingestion |

Container tables describe the request grain, for example
`match_entries_payloads_raw` is one row per event. From unpacking onward, names
describe the entity.

Scrape eligibility is derived, never stored: the existence of a row marks it as
fetched, and the pending work queues are read-only views. Ingestion never reads
dbt output, so a broken or unrun dbt model cannot stop data collection.

## dbt

`dbt/dbt_project.yml` defines the project and the domain-first model paths
(`models/<domain>/staging`, `models/<domain>/marts`). `dbt/profiles.yml` points
at the same PostgreSQL instance and writes models to the `analytics` schema.
Ingestion tables in `ingest` are declared as dbt sources, not models. No models
exist yet; this is only the wiring.

## Troubleshooting

- **`docker: command not found`** - enable Docker Desktop WSL integration (see above).
- **`connection refused` from `tt-db-check`** - the container is not running or
  is still starting. Check `make db-logs` and the compose healthcheck.
- **Logs seem to vanish** - the console is ephemeral by design; read
  `logs/tt_stats.log` or query `ingest.ingest_runs` for what actually happened.
- **Init SQL did not run** - `db/init/*.sql` only executes on the first start of
  an empty volume. Use `make db-reset` to re-run it.
- **dbt cannot connect** - confirm PostgreSQL is up, then `make dbt-debug`.

## Documentation

See `docs/` for architecture notes and ADRs. Local planning notes live outside
this repository.

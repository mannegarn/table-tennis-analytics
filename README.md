# Table Tennis Analytics Platform

End-to-end data platform for international table tennis: scrape the source,
land raw payloads in PostgreSQL, transform with dbt, and analyse with Polars.

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
- `make dbt-debug` ends with `All checks passed!`
- `make test` runs the suite; the integration test skips automatically when the database is down.
- `make test-integration` runs the database integration tests explicitly.

## Events scrape

The events calendar is fetched and upserted into
`ingest.events_by_year_raw`. Each year is one row and one transaction. No
payload files are written. A landing failure marks that year as failed and
makes the command exit non-zero.

```sh
make events-scrape                 # 2021 through the current year
make events-scrape YEARS="2023"    # one year
```

Each row stores the JSONB payload plus the fetch metadata: HTTP status,
`Count`, `Original_Count`, row count, SHA-256 and `fetched_at`.

A terminal uses one compact `tqdm` bar that updates in place, so a long scrape
does not print one line per item. `LOG_JSON=true` emits one JSON object per
event, and piped output keeps the per-year records. Per-year log lines are
suppressed while the bar is active; the database row keeps the detail.

```text
Starting events scrape
  Total Years:  6
  Output:       ingest.events_by_year_raw

Events calendar: 6/6 [##########] year=2026 | saved=174 | api=174 | ok

Events scrape complete
  Total Years:  6
  Failed:       0
```

The run starts with `Starting events scrape` and ends with
`Events scrape complete`, so the beginning and end are always explicit.

Keep a timestamped run log:

```sh
make events-scrape 2>&1 | tee "data/raw/events/run_$(date +%Y%m%dT%H%M%S).log"
```

The 2023 payload is committed at `tests/fixtures/events_2023.json` for the
offline contract test. Run `make db-migrate` first; the scrape writes only to
the database. `data/` remains git-ignored for legacy captures, logs and scratch
files.

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
| `make db-check` | Verify the Python to PostgreSQL connection |
| `make db-migrate` | Apply versioned SQL migrations |
| `make log-demo` | Show the logging output formats: console, JSON and levels |
| `make events-scrape` | Fetch event calendars and land them in `ingest.events_by_year_raw` |
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

`DATABASE_URL` is optional and, when set, overrides the individual parts.

## Project layout

Domain first, then data level.

```
src/tt_stats/core/        shared config, database, logging, clients
src/tt_stats/events/      scrape and land events
src/tt_stats/matches/     scrape and land match entries and details
src/tt_stats/players/     scrape and land player details and playstyle
db/init/                  one-time SQL on first database start
db/migrations/            versioned tables, unpack SQL, views, control tables
dbt/models/<domain>/      marts models per domain, reading _unpacked tables
docs/adr/                 architecture decision records
notebooks/                Polars analytics
app/                      Streamlit dashboard
airflow/dags/             orchestration (later)
```

## Data layers

Raw payloads land in the `ingest` schema as JSONB. SQL unpacks them into columns
at land time, in the same transaction. dbt reads ingestion tables as sources
and writes every model to `analytics`.

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
- **Init SQL did not run** - `db/init/*.sql` only executes on the first start of
  an empty volume. Use `make db-reset` to re-run it.
- **dbt cannot connect** - confirm PostgreSQL is up, then `make dbt-debug`.

## Documentation

See `docs/` for architecture notes and ADRs. Local planning notes live outside
this repository.

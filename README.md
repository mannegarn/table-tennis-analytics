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
make setup   # creates .env from .env.example, then runs uv sync
make db-up   # starts PostgreSQL in the background
make db-check
```

The same steps done manually:

```sh
cp .env.example .env
uv sync
docker compose up -d
uv run tt-db-check
```

## Verification

- `uv run tt-db-check` prints `Connected to PostgreSQL: PostgreSQL 16.4 ...`
- `make db-psql` opens a shell; run `\l` to list databases or `\d` to list tables.
- `make dbt-debug` ends with `All checks passed!`
- `make test` runs the suite; the integration test skips automatically when the database is down.
- `make test-integration` runs the database integration tests explicitly.

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
src/tt_stats/matches/     scrape and land match entries, details and logs
src/tt_stats/players/     scrape and land player details and playstyle
dbt/models/<domain>/      staging and marts models per domain
db/init/                  one-time SQL run on first database start
notebooks/                Polars analytics
app/                      Streamlit dashboard
airflow/dags/             orchestration (later)
```

## Data layers

Raw payloads land in PostgreSQL as JSONB. dbt builds every layer after that.
A single schema is used deliberately; the level lives in the table name.

| Level | Naming | Example |
| --- | --- | --- |
| Raw | `<entity>_raw` | `events_by_year_raw` |
| Staging | `<entity>_staging` | `events_staging` |
| Marts | `<entity>_marts` | `events_marts` |

Container tables describe the request grain, for example
`match_entries_payloads_raw` is one row per event. From staging onward, names
describe the entity.

## dbt

`dbt/dbt_project.yml` defines the project and the domain-first model paths
(`models/<domain>/staging`, `models/<domain>/marts`). `dbt/profiles.yml` points
at the same PostgreSQL instance using the environment variables above. No
models exist yet; this is only the wiring.

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

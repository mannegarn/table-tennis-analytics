# 0014. Pre-computed Parquet snapshots for the Streamlit dashboard

## Status

Accepted (November 2026).

## Context

The project warehouse runs in a local PostgreSQL instance managed via Docker Compose and transformed with dbt (`marts.*`). Publishing the interactive dashboard to Streamlit Community Cloud (`pingpongstats.streamlit.app`) creates three constraints:

1. **No public database exposure:** Streamlit Community Cloud runs statelessly from the GitHub repository without access to the local Docker Postgres instance, and hosting a managed public database purely for read-only dashboard traffic adds unnecessary operational cost and security surface.
2. **Fast cold starts and low memory footprint:** Interactive filtering across events, players, senior singles matches, and game scores should load in sub-second time using Polars without exhausting Streamlit Community Cloud's memory limits.
3. **Multi-GB point logs cannot live in git:** `marts.fct_match_commentary` contains millions of play-by-play rows (`6.77M`+ rows, multiple gigabytes). Shipping raw point logs in the repository would blow past GitHub repository size limits and stall app startup.

## Decision

1. **Export compact Zstandard-compressed Parquet snapshots (`make app-refresh`):**
   - `src/tt_stats/analytics/export_app_data.py` (`uv run tt-app-export`, invoked by `make app-refresh`) queries `marts.*` directly and writes Zstandard-compressed Parquet files (~1.9 MB total) into `app/data/`:
     - `dim_events.parquet`
     - `dim_players.parquet`
     - `fct_senior_singles_matches.parquet`
     - `fct_senior_singles_games.parquet`
     - `catalog_matches_by_event_gender.parquet` (pre-aggregated from `marts.dim_matches`)
   - It also writes `app/data/snapshot_metadata.json` containing `last_collected_at_utc` (derived from `max(finished_at)` where `status = 'succeeded'` in `ingest_wtt.ingest_runs`) and `exported_at_utc` so the dashboard can surface data freshness accurately.
2. **Never export raw point logs:**
   - `marts.fct_match_commentary` is never exported raw to `app/data/`. Any point-level metrics exposed in the dashboard must be pre-aggregated in `src/tt_stats/analytics/` (or a compact summary query) rather than shipping raw commentary rows.
3. **`.gitignore` split for repository hygiene:**
   - Local raw/intermediate data (`/data/`), exploratory `notebooks/`, and generic `*.parquet` files stay gitignored, while `!app/data/*.parquet` explicitly tracks the exported dashboard snapshots alongside `app/data/snapshot_metadata.json`.

## Consequences

**Positive**

- Zero-infrastructure deployment: `pingpongstats.streamlit.app` reads directly from committed `app/data/*.parquet` snapshots with no database credentials, network hops, or Postgres hosting costs.
- Sub-second app loads and deterministic, reproducible dashboard state tied directly to git commits (~1.9 MB total payload).
- Strict separation of concerns: the Streamlit presentation layer only reads curated `marts.*` exports and never touches `ingest_*` or `staging_*`.

**Negative**

- Dashboard updates are not real-time: after running ingestion and `make dbt-run`, the maintainer must run `make app-refresh` and commit the updated `app/data/` artifacts to publish fresh data.
- Detailed rally/point-level features require dedicated pre-aggregation in `src/tt_stats/analytics/` before they can appear in the hosted app.

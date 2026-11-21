# 0012. Layer-and-source (`level_source`) schemas, local run history, and `public` migration ledger

## Status

Accepted (November 2026). Supersedes ADR 0007.

## Context

ADR 0007 put every ingestion table into one `ingest` schema and every dbt model into one
`analytics` schema. That worked while only WTT existed, but the warehouse now holds
separate data sources (WTT and ITTF enrichment) and is designed to add national/team
leagues (Pro A, TTBL, T.League, Superliga, MLTT) without mixing raw payloads, staging
views, or source-specific marts together.

Two control tables also needed a clear home once `ingest` was split by source:

- `ingest_runs` records HTTP fetch attempts for a source's collectors.
- `schema_version` tracks the single numbered migration sequence for the whole database.

## Decision

Organise PostgreSQL schemas and dbt model folders by **`level_source`** (layer first,
source second), with no bare `ingest` or `analytics` schema:

1. **Raw / landing schemas (`ingest_<source>`)**: `ingest_wtt` owns WTT raw tables,
   narrow `*_unpacked` queue tables, `*_pending` views, and `ingest_wtt.ingest_runs`.
   `ingest_ittf` owns `ittf_player_profiles` and `ittf_player_profiles_pending`. Future
   leagues add `ingest_<league>`. Migrations create source schemas themselves so freshly
   created test databases need no bootstrap script.
2. **Local run history (`ingest_wtt.ingest_runs`)**: Each scraped source owns its own
   `ingest_runs` table rather than sharing a cross-source schema. The `endpoint`
   column identifies the API endpoint (e.g. `wtt_eventcalendar`) because the schema
   already names the source. ITTF is a CSV seed and writes no run history; a future
   scraped league adds its own `ingest_<league>.ingest_runs` when needed.
3. **Database-wide migration ledger (`public.schema_version`)**: One linear numbered
   migration sequence (`db/migrations/`) governs the whole database, so its ledger
   lives in `public.schema_version` rather than under any source schema.
4. **dbt schemas (`staging_<source>`, `intermediate`, `marts_<source>`, `marts`,
   `seeds`)**: Every dbt model sets `+schema` and a custom `generate_schema_name` macro
   writes directly to those schema names. The dbt profile default target schema is
   `dbt_default`, which must remain empty so an unconfigured model is immediately caught.

## Consequences

**Positive**

- Every source owns an isolated vertical slice (`ingest_<source>` -> `staging_<source>`
  -> `marts_<source>`), while unified views live cleanly in `marts`.
- Schema names and dbt folder structure match one-to-one.
- Migrations are self-bootstrapping in CI and local test databases without relying on
  `db/init/001_bootstrap.sql`.
- An empty `dbt_default` schema acts as a guardrail against models missing `+schema`.

**Negative**

- Queries and tests must qualify tables with their source-specific schema
  (`ingest_wtt.*`, `ingest_ittf.*`).
- Adding a new scraped source requires creating its `ingest_<source>` schema (and its
  own `ingest_runs` table if it records HTTP fetch history).
- ADR 0007 is no longer current.

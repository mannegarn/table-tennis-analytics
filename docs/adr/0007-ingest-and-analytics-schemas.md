# 0007. Separate ingest and analytics schemas

## Status

Accepted (October 2026). Supersedes ADR 0002.

## Context

ADR 0002 used one schema and put the data level in the table name. That kept
names self-describing, but the boundary between ingestion-owned tables and dbt
output was only a convention. Grants and dbt sources could not express it.

## Decision

Use two PostgreSQL schemas:

- **`ingest`** owns raw payloads, unpacked tables and ingestion control state
  (`schema_version`, later `ingest_runs`).
- **`analytics`** owns dbt output only.
- dbt sources point at `ingest`; the dbt profile writes to `analytics`.
- Ingestion never reads `analytics`.

## Consequences

**Positive**

- The boundary is enforced by schema ownership and grants, not by naming.
- dbt sources are explicit: `ingest.events_by_year_raw`,
  `ingest.events_unpacked`.
- Read-only roles can be granted per schema.
- dbt output can be rebuilt without touching ingestion state.

**Negative**

- Queries must qualify table names with a schema.
- Two schemas must exist before the first migration and dbt run.
- The dbt profile schema must stay `analytics`.
- ADR 0002 is no longer current.

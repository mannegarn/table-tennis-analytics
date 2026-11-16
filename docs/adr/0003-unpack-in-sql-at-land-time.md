# 0003. Narrow queue-key unpack at land time; full unpack in dbt staging

## Status

Accepted (September 2026; revised November 2026).

## Context

Every source API returns JSON, and that JSON must become relational columns before it
can be queried efficiently. Initially we unpacked all fields in SQL at land time, while
in practice some domains (`match_entries`, `match_details`) only unpacked narrow queue
keys. Keeping wide analytical unpacks in `ingest` duplicated column choices between Python
migrations and dbt staging.

## Decision

Split the responsibility cleanly:

1. **Ingestion (`ingest` schema)** stores the raw JSONB payload (`*_raw`) and unpacks **only
   the narrow identity/date columns required by `_pending` scraper queue views**:
   `events_unpacked`, `match_entries_unpacked`, and `match_details_players_unpacked`.
   Sources whose pending views read `*_raw` directly (`event_sponsors_raw`,
   `match_commentary_raw`, `player_details_raw`) have no `_unpacked` table in `ingest`.
2. **dbt staging (`analytics.stg_*`, materialized as `table`)** owns the full analytical
   JSONB unpack: turning raw JSON into every column needed downstream, properly typed and
   trimmed, one-to-one with its source, and guarded by `not_null` and uniqueness tests.

## Consequences

**Positive**

- **Single analytical unpack**, owned in SQL by dbt (`stg_*`), without duplicating wide
  tables in `ingest` (notably avoiding a 2.7 GB duplicate of `match_commentary`).
- **Simple, decoupled ingestion**: scrapers and `_pending` views depend only on raw tables
  and the minimal queue keys they actually read.
- **Re-runnable**: raw JSONB remains the source of truth in `ingest`; changing unpacked
  columns only requires editing a dbt model and running `dbt run`.

**Negative**

- `dbt staging` models that unpack JSONB are materialized as `table`s rather than `view`s.
- Protection against upstream key renames shifts from `ingest` `NOT NULL` constraints to
  dbt schema tests (`not_null`, `unique`, `accepted_values`).

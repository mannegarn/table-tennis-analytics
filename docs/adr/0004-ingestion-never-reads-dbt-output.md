# 0004. Ingestion never reads dbt output; queues derive from raw

## Status

Accepted (September 2026).

## Context

The source API is hierarchical, so ingestion runs in dependent steps:

events, then matches within each event, then details for each match.

Each step needs to know what work remains, which means knowing what already exists.
The tempting shortcut is to read a transformed table to build the next work list, for
example reading `match_entries` to find which matches still need details. That creates
a Python to dbt to Python dependency loop.

## Decision

**Ingestion reads only ingestion-owned tables.** Work queues derive from `_raw` and
`_unpacked` tables and from control tables, never from dbt models.

The queue is exposed as read-only Postgres views owned by ingestion:

`v_years_pending`, `v_events_pending_entries`, `v_matches_pending_details`,
`v_matches_pending_logs`.

dbt reads one way only: raw and unpacked tables in, marts out.

## Consequences

**Positive**

- Ingestion bootstraps on an empty database and can run with dbt never having executed.
- A dbt bug, refactor or failed run cannot halt data collection.
- Ingestion can be re-run independently at any time.
- Ownership stays clean: marts are analytics artefacts, not load-bearing infrastructure.
- Queues avoid the data-loss trap where a mart filter silently excludes rows, meaning
  they are never fetched at all.

**Negative**

- The queue logic must be written in SQL rather than reusing dbt models, so some
  awareness of the payload shape lives in more than one place.
- Running dbt between ingestion steps is still sensible for freshness, but it must
  never become a precondition. That distinction needs discipline to maintain.
- Ordering between ingestion steps becomes explicit in the orchestration layer rather
  than implied by model dependencies.

# 0003. Unpack raw payloads in SQL at land time, owned by ingestion

## Status

Accepted (September 2026).

## Context

Every source API returns JSON, and that JSON must become relational columns before it
can be queried efficiently. Three places could do that work:

1. **Python, at fetch time.** Parsing and typing in the ingestion layer.
2. **dbt, after landing.** Staging models unpack the payload.
3. **SQL, immediately after landing**, in the same transaction.

The scrape queue needs certain fields (event id, name, dates) before the next fetch
step runs, and rebuilding them by un-nesting JSONB on every queue query is slow and
brittle. But moving parsing into Python makes transforms irreversible: a parsing bug
can no longer be fixed by re-running a model.

## Decision

Unpack in **SQL, at land time, in the same transaction as the payload insert**, into a
separate `_unpacked` table owned by ingestion.

One `_unpacked` table per API call set: `events_unpacked`, `match_entries_unpacked`,
`match_details_unpacked`, `player_details_unpacked`, `player_playstyle_unpacked`, and
later `match_logs_unpacked`.

The unpack records **structure only**: JSON to columns. No trimming, no casting beyond
types, no filtering, no domain rules.

The unpack SQL is explicit per entity, versioned in `db/migrations/`. It is idempotent
via `ON CONFLICT DO UPDATE`, scoped to the key just fetched.

## Consequences

**Positive**

- **Re-runnable**, so raw remains the source of truth. A bug means re-running SQL, not
  re-fetching data.
- Python stays thin. It never needs to know a field name.
- The queue becomes an indexed join over narrow tables instead of a payload scan.
- The JSON contract is validated at write time, so upstream field renames fail loudly
  and immediately rather than appearing as silent nulls later.
- One unpack, in one language, reusable by any task.

**Negative**

- A small amount of work appears duplicated, because dbt may also read the same fields.
  That duplication is bounded and cheap: reading bytes twice, not reimplementing logic.
- Two consumers now depend on the unpack contract, so it must be versioned and tested.
- An extra write per fetch, inside the same transaction.
- The old repo's clever generic "generate the flatten SQL from the schema and an
  override map" engine is deliberately **not** rebuilt. Six explicit statements beat
  one generic one that is hard to debug.

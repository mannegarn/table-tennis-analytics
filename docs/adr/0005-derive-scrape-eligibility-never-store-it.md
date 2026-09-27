# 0005. Derive scrape eligibility, never store a scrape flag

## Status

Accepted (September 2026).

## Context

The pipeline needs to know three things about each entity: whether it has already been
fetched, whether it is still live and therefore worth refreshing, and whether it is in
scope at all. The earlier design stored a boolean such as `to_scrape` or `scraped` on
the row.

That is a cache of a query, and caches need invalidation. An ongoing event marked
`to_scrape = false` becomes wrong the next day and needs a maintenance job to correct.
The flag also cannot explain *why* an entity is excluded, and changing policy means
backfilling a flag rather than editing one value.

## Decision

Store **facts**, derive **eligibility**.

- **Already fetched** is represented by row existence, not a column.
- **Still live** is computed from the entity's own dates plus a configurable buffer.
- **Stale** is computed from `refreshed_at`.
- **In scope** is policy, held in configuration.
- **Manual exceptions** are the only stored decision, in `ingest_overrides`.
- **Failures** live in `ingest_runs`, never in a scrape flag.

Eligibility is exposed through the pending views.

## Consequences

**Positive**

- No state to go stale, and no maintenance job needed to correct flags.
- Changing policy is a config edit, not a data migration.
- Reasons are visible in the query, so debugging is straightforward.
- Raw tables record what happened rather than what is intended, which keeps them
  trustworthy as a source of truth.
- An entity with no row remains eligible regardless of age, so old failures retry.

**Negative**

- Eligibility is recomputed on every queue read, which costs a little compared to
  reading a boolean.
- The pending views carry real logic and must themselves be tested.
- Configuration values, such as the date buffer and staleness window, need documenting
  so their purpose is not lost.

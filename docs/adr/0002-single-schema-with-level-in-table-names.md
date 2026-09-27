# 0002. Single schema, data level encoded in table names

## Status

Accepted (September 2026).

## Context

Two conventions were considered for expressing the data level:

1. Separate Postgres schemas per level: `raw`, `staging`, `intermediate`, `marts`.
2. A single schema with the level in the table name: `events_by_year_raw`,
   `events_unpacked`, `events_marts`.

The pipeline is also organised domain-first in the codebase, because the domains
depend on each other in sequence (events, then matches within events, then players
within matches).

## Decision

Use a **single Postgres schema** and encode the level in the table name.

Folders express the domain, then the stage. Table names express the entity, then the
level. Domain-first in folders, level-first in names.

Container tables describe the **request** grain (`events_by_year_raw` is one row per
year). From unpacking onward, names describe the **entity** (`events_unpacked` is one
row per event).

## Consequences

**Positive**

- Every table name is self-describing, so no query needs to know which schema holds
  a given level.
- The domain ordering of the pipeline stays visible in the folder tree, which is
  where it actually matters, rather than being scattered across schemas.
- Simpler for a personal project, with one namespace and no search path concerns.

**Negative**

- Table names are longer than they would be under a schema-per-level approach.
- It is a less common convention than layered schemas, so it needs explaining once
  in a README rather than being assumed.

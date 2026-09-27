# 0001. Postgres and dbt as the warehouse and transform stack

## Status

Accepted (September 2026).

## Context

The earlier version of this project used SQLite with hand-written transform SQL and
file-based raw storage. That worked for a personal script but proved nothing about
warehouse practice, and it mixed transformation logic across Python and SQL with no
clear ownership.

The target is a portfolio-grade platform that demonstrates real analytics engineering:
a relational warehouse, a declarative transform layer with lineage and tests, and
ingestion that stays thin.

## Decision

- **PostgreSQL 16** is the warehouse, run locally in Docker.
- **dbt** owns every transformation after raw landing. No hand-rolled transform SQL.
- **Python** only fetches from the source API and lands raw payloads.
- **Polars** is used for analytics and notebooks only, never inside the pipeline.

## Consequences

**Positive**

- dbt brings lineage, documentation, tests and re-runnable transformations for free.
- Raw payloads stay immutable, so any transform bug is fixable by re-running a model.
- The dbt project is retargetable to BigQuery or Snowflake later by swapping the
  adapter and profile, with minor dialect changes.
- The stack is the one hiring managers actually recognise.

**Negative**

- Two languages and two tools to learn and maintain.
- dbt-postgres does not support Python models, so Polars cannot be used inside dbt.
  This is the direct reason for the ingestion and transform split.
- Local development requires Docker running.

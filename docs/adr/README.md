# Architecture Decision Records

Short records of decisions that shape this project, why they were taken, and what
they cost. One file per decision, numbered, never edited after acceptance. If a
decision changes, add a new ADR that supersedes the old one.

Format: Status, Context, Decision, Consequences. Kept deliberately brief.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-postgres-and-dbt-as-the-warehouse-stack.md) | Postgres and dbt as the warehouse and transform stack | Accepted |
| [0002](0002-single-schema-with-level-in-table-names.md) | Single schema, data level encoded in table names | Accepted |
| [0003](0003-unpack-in-sql-at-land-time.md) | Unpack raw payloads in SQL at land time, owned by ingestion | Accepted |
| [0004](0004-ingestion-never-reads-dbt-output.md) | Ingestion never reads dbt output; queues derive from raw | Accepted |
| [0005](0005-derive-scrape-eligibility-never-store-it.md) | Derive scrape eligibility, never store a scrape flag | Accepted |
| [0006](0006-fetch-all-match-details-defer-match-logs.md) | Fetch all match details; defer match logs | Accepted |

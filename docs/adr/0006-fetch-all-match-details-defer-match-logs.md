# 0006. Fetch all match details; defer match logs

## Status

Accepted (September 2026).

## Context

The previous design filtered out youth, junior, cadet, veteran and para events in
Python **before** scraping, to limit API calls. But the locked architecture states that
business filtering belongs in dbt, which created a conflict: landing everything raw
means paying an API call for every filtered event too.

The expensive step is the fan-out from match entries to per-match data, since that is
one request per match. The two payload types have very different sizes: match details
are modest, while point-by-point match logs are large.

## Decision

- **Match details: fetch everything.** No event-category filter.
- **Match logs: deferred to the end of the project.** Not built now.
- Filtering, when it eventually happens, moves to the **log fan-out only**.
- If prioritisation is wanted before then, **order the queue** rather than filtering it.

## Consequences

**Positive**

- Completeness is preserved. No analysis is blocked by data that was never collected.
- Silent filtering, the worst failure mode because it never errors, is removed from
  the pipeline.
- The only piece of business logic in the ingestion path disappears, so all semantics
  live in dbt as originally intended.
- No exclusion configuration, no policy override mechanism, no filter tests.

**Negative**

- The **initial backfill of match details will be slow**, since it is one request per
  match across the whole history. This is a one-off cost. Mitigations: the watermark
  makes runs resumable, and the client keeps its concurrency, jitter and chunking.
- Storage grows, particularly once match logs are added. Payload sizes should be
  measured rather than assumed before that decision is made.
- Valuable data may arrive later than it otherwise would, which is why queue ordering
  is preferred over filtering.

## Notes

Requirements for the deferred match logs work, so it stays cheap to add later:

- it slots in as one more `_raw` and `_unpacked` pair, one unpack statement, one queue
  view and one collect/land module, with no changes to existing tables or code
- capture one real payload and measure its size before deciding on any filter

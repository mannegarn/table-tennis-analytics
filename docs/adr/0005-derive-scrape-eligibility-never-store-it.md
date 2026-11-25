# 0005. Derive scrape eligibility, never store a scrape flag

## Status

Accepted (September 2026; revised November 2026).

## Context

The pipeline needs to know two main things about each entity: whether it has
already been fetched, and whether it is still live or needs one final refresh
after finishing. Storing a boolean such as `to_scrape` or `scraped` on the row
creates a cache of a query that goes stale the next day, cannot explain *why*
an entity is queued, and requires backfilling flags whenever rules change.

## Decision

Store **facts**, derive **eligibility** in read-only `ingest_wtt.*_pending` SQL views:

- **Already fetched** is represented by row existence in `ingest_wtt.*_raw`, not a flag column.
- **Event deduplication and live window** live in `ingest_wtt.events_latest`, which collapses multi-year sightings in `ingest_wtt.events_unpacked` via `DISTINCT ON (event_id)` (`last_seen_at DESC, source_year DESC`) and computes `window_end = GREATEST(start_date, end_date) + 1` in UTC (a hard-coded 1-day grace window).
- **Stale** is computed from `fetched_at`: a finished event/match whose last fetch landed on or before `window_end` is queued once more (`reason = 'stale'`), and skipped once `fetched_at::date > window_end`. Player profiles in `ingest_wtt.player_details_pending` refresh on a 90-day cadence (`fetched_at < CURRENT_TIMESTAMP - INTERVAL '90 days'` as `reason = 'stale'`), while `tt-player-details-collect` prioritizes `reason = 'new'` ahead of `reason = 'stale'`.
- **In scope** is policy in configuration (such as `EVENTS_START_YEAR=1926`) and pending-view filters (`start_date <= today` in UTC).
- **Empty/404 protection**: non-empty raw payloads cannot be overwritten by 404 placeholders or empty responses (`{}`, `[]`, `{"Result": []}`), so `payload` and `fetched_at` are preserved when a transient empty response arrives.
- **Failures and cooldowns** live in `ingest_wtt.ingest_runs`, never in a scrape flag. `ingest_wtt.ingest_run_cooldowns` derives consecutive unrecovered failures within the last 7 days per `(endpoint, entity_key)` (failed runs with no newer succeeded run) and applies exponential backoff before re-queuing: `1h` for 1 failure, `4h` for 2, `24h` for 3, and `7d` for 4+.

## Consequences

**Positive**

- No scrape flags to go stale, and no maintenance job needed to reset them.
- Reasons (`live`, `new`, `stale`, `current`, `next`, `backfill`) are computed directly in the view, making queue logs easy to debug.
- Raw tables record real fetch facts rather than intent.
- An entity that fails before landing has no row yet, so it stays eligible after its failure cooldown expires (while a blocked empty overwrite preserves the old `fetched_at` so stale retries stay eligible).

**Negative**

- Eligibility is recomputed on every queue read, which costs slightly more than reading a stored flag.
- The 1-day event grace window and 90-day player refresh cadence are hard-coded in the pending SQL views (`018`) rather than configurable at runtime, and event windows compare venue-local event dates against UTC `today`.

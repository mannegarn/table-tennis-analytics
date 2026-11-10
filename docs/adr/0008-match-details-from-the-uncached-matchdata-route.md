# 0008. Match details come from the uncached matchdata route

## Status

Accepted (November 2026).

## Context

Three candidate endpoints exist for one match, all keyed by the same pair we
already store: `eventId` and `documentCode`.

| # | Endpoint | What it returns |
| --- | --- | --- |
| 1 | `/primary/api/match/commentary/<eventId>/<documentCode>` | a **JSON string** of point-by-point commentary rows, ~114 KB |
| 2 | `wtt-web-frontdoor-…/matchdata/matchcentre/<eventId>/<documentCode>.json` | the match summary **plus** `full_msg`, the whole point-by-point message log |
| 3 | `wtt-web-frontdoor-withoutcache-…/matchdata/<eventId>/<documentCode>.json` | the same match summary with `full_msg: null` |

Probed against event 3257 / `TTEWSINGLES-----------RND1002700----------`:

- **2 and 3 have an identical 25-key schema and identical values, except
  `full_msg`**: 176,505 bytes on the cached route versus 4 bytes (`null`) on the
  uncached one.
- Decoded payload: **160,252 bytes** for 2, **1,880 bytes** for 3.
- The summary carries everything a match record needs: competitors,
  `gameScores: "9-11,11-8,11-5,7-11,11-8"`, `resultStatus: OFFICIAL`, `action`
  (server, receiver, next server), `matchConfig`, `venueName`, `tableName`,
  `matchDateTime`, and `subEventDescription`, which restates the round encoded in
  `documentCode` ("Women's Singles - Qualifying Round 1 - Match 27").
- Both routes answer `content-encoding: br` regardless of what the request asks
  for, and both take a cache-busting `q`.
- Match logs are already deferred by ADR 0006.

## Decision

Fetch match details from **route 3, the uncached matchdata route**, and store its
response as sent.

- **`full_msg` is the only reason routes 2 and 3 differ**, and it is a match log.
  At ~176 KB per match across ~200k matches that is tens of gigabytes of data
  already deferred by ADR 0006; route 2 would smuggle it back in under another
  name.
- **Route 1 (commentary) is the same log in another shape**, so it stays with the
  deferred work too.
- **No live-match refresh behaviour is designed for.** This is a historical
  archive, not a live tracker; the uncached host is chosen for its payload size,
  not its freshness.

## Consequences

**Positive**

- Roughly **85× smaller** per match than the cached route, with no information
  lost that this project wants.
- One call per match yields the whole summary: scores, competitors and result
  status.
- Match logs keep exactly one home for when they are eventually collected,
  instead of being half-present inside the details payload.

**Negative**

- When match logs are collected they need their own fan-out; nothing here is
  reused.
- The route name suggests a deliberate cache bypass. That is fine for a backfill,
  but a re-run is not free at the edge and must be paced like the other
  collectors.
- `brotli` is now a dependency: these hosts answer `br` even when `identity` is
  requested, so a client without a brotli decoder receives unreadable bytes.

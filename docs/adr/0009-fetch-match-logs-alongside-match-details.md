# 0009. Fetch match logs alongside match details

## Status

Accepted (November 2026). Supersedes ADR 0008 and the deferral of point-by-point
data in ADR 0006.

## Context

ADR 0006 deferred point-by-point data because it is large and no analysis needed
it yet. That is no longer true: serve-win rates, deuce behaviour and the
best-of-five effect all require **who served each point and who won it**.

Three endpoints were probed for event 3257 /
`TTEWSINGLES-----------RND1002700----------`:

| Endpoint | Content | Size |
| --- | --- | --- |
| commentary | 263 action rows: `SSR` service changes, `PT` point winners with running game and match score, `GPOINT`/`MPOINT` | ~114 KB JSON string |
| matchcentre | match summary **plus** `full_msg` | ~160 KB decoded |
| withoutcache | the same summary with `full_msg: null` | 1.9 KB |

`full_msg` is the **official ITTF result document**: a dict of
`{PlayByPlay, Results_all}` where `Results_all` held 14 `DT_RESULT` messages, each
with `Competition` (17 KB: `ExtendedInfos`, `UnitDateTime`, `SportDescription`,
`PeriodDescription`), `DocumentCode`, `ResultStatus`, `SequenceNumber`. In this
match `PlayByPlay` was **null**, so the official document alone cannot answer
"who served this point".

The commentary rows can. Extracts:

```text
SSR    Service change: YOVKOVA Maria to serve
PT     Point won by ERKEBAEVA Asel, Game 1, Game Score 0-1, Match Score 0-0
GPOINT GAME POINT: 1 to ERKEBAEVA Asel, Game Score 9-10, Match Score 0-0
MPOINT Match POINT: YOVKOVA Maria, Game 5, Game Score 10-8, Match Score 2-2
```

Server comes from `SSR`, winner and score from `PT`, and deuce/game-point context
from `GPOINT`/`MPOINT`. All rows carry `SEQUENCENUMBER` and UTC/local timestamps.

## Decision

Fetch **both** documents per match and store both raw:

- **matchcentre** (`wtt-web-frontdoor-…/matchdata/matchcentre/…`), because it is
  the only source of the official result document, and it already contains the
  match summary.
- **commentary** (`…/primary/api/match/commentary/<eventId>/<documentCode>`),
  because it is the point stream that serve and deuce analysis depends on.

Point-by-point data is therefore **back in scope**, as `match_logs_raw` and later
`match_logs_unpacked`, sibling to `match_details` exactly as ADR 0003 named them.

## Consequences

**Positive**

- The intended analyses become possible: serve win rate overall and by game,
  deuce performance, best-of-five effects, and momentum from the sequence.
- One match yields both the official record and the point-level detail, keyed by
  the same `(eventId, documentCode)` the match-entries payload already provides.
- `full_msg` is stored raw, so `PlayByPlay` is available for the matches where
  upstream populates it, without a schema change.

**Negative**

- **Volume.** Roughly 275 KB per match raw (160 KB + 114 KB). Across ~200,879
  matches that is **tens of gigabytes** before PostgreSQL compression, which
  should bring it down several fold. This must be measured on a sample before the
  full fan-out, and the disk budget confirmed.
- **Two requests per match**, so the fan-out roughly doubles, and the commentary
  host is a different front door with its own pacing.
- The commentary host needs the ITTF-style headers (`secapimkey`, browser agent),
  and every one of these hosts answers `br`, so the brotli decoder is mandatory.
- Parsing the point stream is non-trivial: `SSR` establishes the server, and game
  and match scores must be tracked from `PT` text or recomputed. That parsing is
  a dbt/Polars concern, not ingestion, so raw storage of the string payload is
  what keeps it reversible.

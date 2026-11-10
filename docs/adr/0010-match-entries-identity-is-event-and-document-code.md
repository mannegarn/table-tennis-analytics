# 0010. A match is identified by its event and document code, not by its id

## Status

Accepted (November 2026). Corrects the identity assumption in migration 005,
which was amended in place the same day while the database was still disposable.

## Context

Migration 005 keyed `ingest.match_entries_unpacked` on `match_id`, on the
reasonable assumption that WTT's `id` field is a global match identifier. No
unpack existed yet, so nothing had tested that assumption.

Before writing the unpack, all 2838 landed payloads were measured — 200,879
match rows:

- **`id` repeats inside one event.** 278 groups, 943 rows: event 2411 sends
  id 59820 twice, once as `TTEWSINGLES-----------QFNL000100` and once as
  `...QFNL000200`. These are different matches.
- **`id` repeats across events.** 132 ids appear under two or three
  tournaments (2553/2554, 2632/2634), so it is not global either.
- **`id` is sometimes 0.** 183 team tie cards in event 2345.
- **`documentCode` is unique within an event.** 200,879 distinct
  `(event_id, documentCode)` pairs, zero repeats.
- **The id key itself is inconsistent.** 854 rows in 7 payloads spell it `iD`.
- **424 rows carry no `startDateLocal`**, and 1665 carry the sentinel
  `0001-01-01T00:00:00`.

Keying on `match_id` would therefore have failed on the first real unpack, and
guessing that a repeated id meant a duplicate row would have silently deleted a
quarter-final.

## Decision

- **Identity is `(event_id, document_code)`.** The primary key moves there.
- **`match_id` becomes a fact, not a key.** It is still unpacked — the deferred
  match-detail fan-out looks matches up by it (ADR 0006, ADR 0009) — and is
  indexed, but it may repeat and may be 0.
- **Id 0 rows are kept.** The check allows `>= 0` and refuses only negatives.
  They are not fetchable, but they are what the payload said.
- **`start_date_local` becomes nullable**, storing blank as NULL. The
  `0001-01-01` sentinel is stored as sent; interpreting it is a dbt rule
  (ADR 0003).
- **Migration 005 is amended in place, and the database rebuilt.** It was first
  written as an additive 007 so the landed payloads would survive; once it was
  clear the payloads could be re-dumped and replayed, the correction was folded
  back into 005. One coherent table definition reads better than a table plus an
  immediate patch, and the plan's declarative rule still holds today.

## Consequences

**Positive**

- The key is derived from measurement rather than assumption, and is the one
  field the API itself uses to distinguish two matches in the same draw.
- Repeats are visible as distinct rows, so no match is silently dropped.
- The fan-out still has the index it needs, without pretending the id is unique.

**Negative**

- Downstream code must use `(event_id, document_code)` for identity and treat
  `match_id` as an attribute; `WHERE match_id = ...` returns several rows for a
  handful of ids.
- The 183 team cards are stored but unusable until team ties get their own
  treatment.
- Amending 005 means the first version of that file, which keyed on `match_id`,
  no longer exists in history: readers see the corrected definition only, with
  the measurements that produced it in the comments.

"""Fetch and land one WTT match list per event.

The endpoint returns the whole match list for one tournament, so the request
grain is the event and the payload is stored as one row in
``ingest.match_entries_payloads_raw``. Which events are asked for is derived in
``ingest.match_entries_pending`` (ADR 0005), never stored.

Two shapes behave differently from the calendar collector:

* **The response is a bare JSON array**, not a ``rows`` envelope, and an empty
  answer is a plain ``[]`` of two bytes. That is a success, not a failure.
* **Each payload is small but there are thousands of them**, so batching and
  concurrency matter here in a way they never did for 102 calendar requests.

Every attempt, successful or not, is recorded in ``ingest.ingest_runs`` with
``source = wtt_getofficialresult`` and ``entity_key = event_id``.
"""

import argparse
import asyncio
import hashlib
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, cast

from tt_stats.core.batching import chunks
from tt_stats.core.clients.wtt_api import (
    WttClient,
    WttPayloadError,
    WttRequestError,
    WttResponse,
)
from tt_stats.core.config import get_settings
from tt_stats.core.db import get_connection
from tt_stats.core.ingest_runs import (
    SOURCE_WTT_MATCH_ENTRIES,
    RunRecord,
    record_run,
    status_for,
)
from tt_stats.core.logging_config import configure_logging, get_logger
from tt_stats.core.progress import RunTiming, plural, progress_bar
from tt_stats.matches.land import land_match_entries

logger = get_logger(__name__)

#: The match id arrives under ``id`` or ``iD`` depending on the event: 3098 and
#: the 2021-2022 events send ``id``, events 3257 and 3346 send ``iD``. Same
#: integer, inconsistent casing upstream, so both are accepted here and the
#: unpack coalesces them into one column.
MATCH_ID_KEYS = ("id", "iD")

#: Keys every match row carries, checked before landing so an upstream rename
#: fails the event instead of filling the unpack with NULLs later.
REQUIRED_MATCH_KEYS = (
    "eventId",
    "documentCode",
    "subEventType",
    "fullResults",
    "startDateLocal",
)

#: The queue, in the order the view defines: live events first, then finished
#: events by most recent end date. ``--limit`` takes a prefix of this.
_PENDING_EVENTS_SQL = """
WITH bounds AS (
    SELECT current_date AS today
)
SELECT p.event_id, p.reason
FROM ingest.match_entries_pending AS p
CROSS JOIN bounds AS b
ORDER BY
    (p.end_date + 1 >= b.today) DESC,
    p.end_date DESC,
    p.event_id;
"""


class PayloadShapeError(ValueError):
    """The response is not the bare array of match rows the endpoint sends."""


LandingFn = Callable[["MatchEntriesSummary", Any], None]
RecordRunFn = Callable[[RunRecord], None]
ProgressFn = Callable[["MatchEntriesSummary"], None]


@dataclass(frozen=True, slots=True)
class MatchEntriesSummary:
    """What happened for one event, including the evidence for a failure."""

    event_id: int
    status_code: int | None
    #: Match rows parsed from the payload. 0 is legitimate and is stored.
    received_count: int
    payload_sha256: str | None
    fetched_at: datetime
    error: str | None
    #: Why the queue asked: live, new or stale. None for an explicit event id.
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class _Fetched:
    """One settled network attempt, before validation and landing."""

    event_id: int
    fetched_at: datetime
    response: WttResponse | None
    error: str | None
    status_code: int | None
    reason: str | None


def pending_events(limit: int | None = None) -> list[tuple[int, str]]:
    """Return ``(event_id, reason)`` pairs to fetch, in queue order.

    Args:
        limit: Optional cap, applied after ordering, so a smoke run picks the
            live events rather than an arbitrary slice.
    """
    with get_connection() as connection:
        rows = connection.execute(_PENDING_EVENTS_SQL).fetchall()

    queue = [(int(row["event_id"]), str(row["reason"])) for row in rows]
    return queue[:limit] if limit is not None else queue


def extract_rows(payload: object) -> list[dict[str, Any]]:
    """Return the match rows from the payload.

    An empty list is valid: many events, and everything before late 2021, have
    no matches at all.

    Raises:
        PayloadShapeError: If the payload is not a list of objects.
    """
    if not isinstance(payload, list):
        raise PayloadShapeError(
            f"expected a list of match rows, got {type(payload).__name__}"
        )

    rows: list[dict[str, Any]] = []
    for index, row in enumerate(cast("list[object]", payload)):
        if not isinstance(row, dict):
            raise PayloadShapeError(f"row {index} is not an object")
        rows.append(cast("dict[str, Any]", row))

    return rows


def match_id_of(row: Mapping[str, Any]) -> int | None:
    """Return the match id from ``id`` or ``iD``, whichever the row uses.

    Returns None when neither key holds an integer, which is a contract problem
    rather than a match without an id.
    """
    for key in MATCH_ID_KEYS:
        value = row.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def validate_rows(
    rows: Sequence[dict[str, Any]],
    *,
    event_id: int,
) -> list[str]:
    """Return a list of contract problems, empty when every row is valid.

    The ``eventId`` check is the important one: it proves the request returned
    the event that was asked for. Without it, a wrong query parameter would land
    one event's matches under another event's id and nothing else would notice.
    """
    problems: list[str] = []

    for index, row in enumerate(rows):
        missing = [key for key in REQUIRED_MATCH_KEYS if key not in row]
        if missing:
            problems.append(f"row {index} missing keys: {', '.join(missing)}")
            continue

        if match_id_of(row) is None:
            problems.append(f"row {index} has no integer id or iD")

        if str(row.get("eventId")) != str(event_id):
            problems.append(
                f"row {index} eventId {row.get('eventId')!r} does not match "
                f"requested event {event_id}"
            )

    return problems


async def collect_match_entries(
    events: Sequence[int],
    *,
    client: WttClient,
    progress: ProgressFn | None = None,
    land: LandingFn | None = None,
    record: RecordRunFn | None = None,
    chunk_size: int | None = None,
    reasons: Mapping[int, str] | None = None,
) -> list[MatchEntriesSummary]:
    """Fetch and land every requested event, one chunk at a time.

    Each chunk is fetched concurrently and then landed sequentially: landing is
    synchronous psycopg work, one transaction per event, so nothing is shared
    between events and the log order stays deterministic. One failed event never
    stops the others.

    Args:
        events: Event ids to fetch, already in priority order.
        client: The async WTT client. Its semaphore bounds requests in flight.
        progress: Called with each settled summary, so the caller can draw a bar.
        land: Called with each successful summary and its payload.
        record: Called with a ``RunRecord`` per settled attempt.
        chunk_size: Events fetched per batch. ``None`` means "one batch".
        reasons: Queue reason per event id, for logging and the bar postfix.

    Raises:
        ValueError: If ``chunk_size`` is not a positive integer.
    """
    summaries: list[MatchEntriesSummary] = []
    size = chunk_size if chunk_size is not None else max(len(events), 1)
    reason_for = reasons or {}
    batches = chunks(list(events), size)

    for index, chunk in enumerate(batches, start=1):
        chunk_started = datetime.now(tz=UTC)
        outcomes = await asyncio.gather(
            *(
                _fetch_one(event_id, client=client, reason=reason_for.get(event_id))
                for event_id in chunk
            ),
            # One broken coroutine must not cancel its siblings; it becomes a
            # failed event with the exception as the reason.
            return_exceptions=True,
        )

        settled: list[MatchEntriesSummary] = []
        for event_id, outcome in zip(chunk, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                fetched = _Fetched(
                    event_id=event_id,
                    fetched_at=datetime.now(tz=UTC),
                    response=None,
                    error=f"{type(outcome).__name__}: {outcome}",
                    status_code=None,
                    reason=reason_for.get(event_id),
                )
            else:
                fetched = outcome

            summary = _settle(fetched, land=land, record=record)
            settled.append(summary)
            summaries.append(summary)
            if progress is not None:
                progress(summary)

        _log_chunk(
            index,
            len(batches),
            settled,
            started_at=chunk_started,
        )

    return summaries


async def _fetch_one(
    event_id: int,
    *,
    client: WttClient,
    reason: str | None,
) -> _Fetched:
    """Fetch one event's match list and describe the outcome without raising."""
    fetched_at = datetime.now(tz=UTC)

    try:
        response = await client.fetch_match_entries(event_id)
    except WttRequestError as error:
        return _Fetched(
            event_id=event_id,
            fetched_at=fetched_at,
            response=None,
            error=str(error),
            status_code=error.status_code,
            reason=reason,
        )
    except WttPayloadError as error:
        return _Fetched(
            event_id=event_id,
            fetched_at=fetched_at,
            response=None,
            error=str(error),
            status_code=None,
            reason=reason,
        )

    return _Fetched(
        event_id=event_id,
        fetched_at=fetched_at,
        response=response,
        error=None,
        status_code=response.status_code,
        reason=reason,
    )


def _settle(
    fetched: _Fetched,
    *,
    land: LandingFn | None,
    record: RecordRunFn | None,
) -> MatchEntriesSummary:
    """Validate, land and record one fetched event."""
    if fetched.response is None:
        summary = MatchEntriesSummary(
            event_id=fetched.event_id,
            status_code=fetched.status_code,
            received_count=0,
            payload_sha256=None,
            fetched_at=fetched.fetched_at,
            error=fetched.error or "request failed",
            reason=fetched.reason,
        )
        _log_summary(summary)
        _record_run(summary, record)
        return summary

    response = fetched.response
    digest = hashlib.sha256(response.content).hexdigest()

    received_count = 0
    error_message: str | None = None

    try:
        rows = extract_rows(response.data)
        received_count = len(rows)
        problems = validate_rows(rows, event_id=fetched.event_id)
        if problems:
            error_message = "row validation failed: " + "; ".join(problems[:5])
    except PayloadShapeError as error:
        error_message = f"payload shape error: {error}"

    summary = MatchEntriesSummary(
        event_id=fetched.event_id,
        status_code=response.status_code,
        received_count=received_count,
        payload_sha256=digest,
        fetched_at=fetched.fetched_at,
        error=error_message,
        reason=fetched.reason,
    )

    if summary.error is None and land is not None:
        try:
            land(summary, response.data)
        except Exception as error:
            summary = replace(summary, error=f"landing failed: {error}")

    _log_summary(summary)
    _record_run(summary, record)
    return summary


def _log_chunk(
    index: int,
    total: int,
    settled: Sequence[MatchEntriesSummary],
    *,
    started_at: datetime,
) -> None:
    """Log one checkpoint per chunk, rather than one line per event.

    A backfill is ~3000 events, so per-event records would bury the log. A chunk
    line still shows progress, throughput and the failure count, and there are
    only a few dozen of them. Per-event detail moves to DEBUG.
    """
    matches = sum(summary.received_count for summary in settled)
    empty = sum(
        1 for summary in settled if not summary.error and summary.received_count == 0
    )
    failed = sum(1 for summary in settled if summary.error)
    elapsed = (datetime.now(tz=UTC) - started_at).total_seconds()

    logger.info(
        "match_entries_chunk",
        extra={
            "chunk": index,
            "chunks": total,
            "events": len(settled),
            "matches": matches,
            "empty": empty,
            "failed": failed,
            "chunk_seconds": round(elapsed, 3),
            "chunk_rate_per_second": round(len(settled) / elapsed, 2) if elapsed else 0,
        },
    )


def _progress_message(summary: MatchEntriesSummary) -> str:
    if summary.error:
        state = "failed"
    elif summary.received_count == 0:
        state = "empty"
    else:
        state = "ok"
    reason = f"{summary.reason} " if summary.reason else ""
    return (
        f"{reason}event={summary.event_id} | matches={summary.received_count} | {state}"
    )


def _record_run(
    summary: MatchEntriesSummary,
    record: RecordRunFn | None,
) -> None:
    """Write the attempt to ``ingest.ingest_runs``, never breaking the collect."""
    if record is None:
        return

    try:
        record(
            RunRecord(
                source=SOURCE_WTT_MATCH_ENTRIES,
                entity_key=str(summary.event_id),
                status=status_for(summary.error),
                started_at=summary.fetched_at,
                http_status=summary.status_code,
                received_count=summary.received_count,
                payload_sha256=summary.payload_sha256,
                error=summary.error,
            )
        )
    except Exception as error:
        # Recording is diagnostics: losing a row must not lose the payload.
        logger.warning(
            "ingest_run_not_recorded",
            extra={"event_id": summary.event_id, "error": str(error)},
        )


def _log_summary(summary: MatchEntriesSummary) -> None:
    extra: dict[str, Any] = {
        "event_id": summary.event_id,
        "reason": summary.reason,
        "status": summary.status_code,
        "received_count": summary.received_count,
    }
    if summary.error:
        logger.error(
            "match_entries_incomplete",
            extra={**extra, "error": summary.error},
        )
    elif summary.received_count == 0:
        logger.debug("match_entries_empty", extra=extra)
    else:
        logger.debug("match_entries_saved", extra=extra)


async def _run(
    events: Sequence[int],
    progress: ProgressFn | None,
    *,
    chunk_size: int,
    reasons: Mapping[int, str] | None,
) -> list[MatchEntriesSummary]:
    async with WttClient() as client:
        return await collect_match_entries(
            events,
            client=client,
            progress=progress,
            land=land_match_entries,
            record=record_run,
            chunk_size=chunk_size,
            reasons=reasons,
        )


def _reason_counts(queue: Sequence[tuple[int, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _, reason in queue:
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-matches-collect``."""
    parser = argparse.ArgumentParser(
        description=(
            "Fetch WTT match lists into ingest.match_entries_payloads_raw. "
            "Without --event-ids, the pending view decides: live events, then "
            "events never fetched, then events whose snapshot predates their "
            "final day."
        )
    )
    parser.add_argument(
        "--event-ids",
        nargs="+",
        type=int,
        default=None,
        help="Explicit event ids to fetch, bypassing the pending view.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Fetch at most this many events from the queue, in queue order.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=None,
        help="Events fetched per batch. Defaults to Settings.chunk_size.",
    )
    args = parser.parse_args(argv)

    # Argparse-style validation, before any Settings or database work: a typo in
    # a limit should not need a database password to be reported.
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.chunk_size is not None and args.chunk_size < 1:
        parser.error("--chunk-size must be at least 1")

    configure_logging()
    settings = get_settings()

    reasons: dict[int, str] = {}
    if args.event_ids is not None:
        events = list(args.event_ids)
        logger.info("match_entries_manual", extra={"events": len(events)})
    else:
        queue = pending_events(args.limit)
        events = [event_id for event_id, _ in queue]
        reasons = dict(queue)
        logger.info(
            "match_entries_queue",
            extra={"events": len(events), "reasons": _reason_counts(queue)},
        )

    if not events:
        logger.info("match_entries_nothing_pending")
        print("Nothing pending: every event is already fetched.")  # noqa: T201
        return 0

    if settings.log_level.upper() != "DEBUG":
        # One line per HTTP request would bury the audit trail.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    chunk_size = args.chunk_size or settings.chunk_size
    chunks_total = -(-len(events) // chunk_size)
    started_at = datetime.now(tz=UTC)
    logger.info(
        "match_entries_started",
        extra={
            "events": len(events),
            "chunk_size": chunk_size,
            "chunks": chunks_total,
            "concurrency": settings.max_concurrent_requests,
        },
    )

    # The bar owns one stdout line and rewrites it. Routine records are INFO, so
    # the WARNING-level console handler cannot push it down a line; they still
    # land in logs/tt_stats.log.
    with progress_bar(
        len(events),
        description=(
            f"Collecting match entries "
            f"({chunk_size}/chunk, {settings.max_concurrent_requests} workers)"
        ),
        unit="event",
        disable=settings.log_json,
    ) as bar:

        def advance(summary: MatchEntriesSummary) -> None:
            bar.set_postfix_str(_progress_message(summary), refresh=False)
            bar.update(1)

        results = asyncio.run(
            _run(
                events,
                advance,
                chunk_size=chunk_size,
                reasons=reasons or None,
            )
        )

    timing = RunTiming(started_at, datetime.now(tz=UTC))
    failed = [result for result in results if result.error]
    empty = [
        result for result in results if not result.error and result.received_count == 0
    ]
    matches = sum(result.received_count for result in results)
    tally: dict[str, Any] = {
        "events": len(results),
        "failed": len(failed),
        "empty": len(empty),
        "matches": matches,
        "elapsed_seconds": round(timing.elapsed.total_seconds(), 3),
        "rate_per_second": round(timing.rate_per_second(len(results)), 3),
    }
    if failed:
        logger.error("match_entries_incomplete", extra=tally)
    else:
        logger.info("match_entries_complete", extra=tally)

    # Deliberate stdout write: terminal logging sits at WARNING, so the record
    # above never reaches the console. This is the run summary, not a log line.
    print(  # noqa: T201
        f"{timing.describe(len(results), unit='event')}, "
        f"{chunks_total:,} {plural(chunks_total, 'chunk')}: "
        f"{matches:,} matches found, "
        f"{len(empty):,} {plural(len(empty), 'event')} with no matches, "
        f"{len(failed):,} failed"
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

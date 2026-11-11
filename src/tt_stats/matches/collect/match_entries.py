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

from __future__ import annotations

import argparse
import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from tt_stats.core.batching import FetchedItem, collect_in_chunks, reason_counts
from tt_stats.core.cli import (
    add_queue_arguments,
    chunk_count,
    log_chunk_progress,
    progress_message,
    quiet_http_logs,
    report_collect_run,
    run_with_progress,
    validate_queue_arguments,
)
from tt_stats.core.clients.wtt_api import WttClient
from tt_stats.core.config import get_settings
from tt_stats.core.db import get_connection
from tt_stats.core.ingest_runs import (
    SOURCE_WTT_MATCH_ENTRIES,
    RunRecord,
    record_run,
    safe_record_run,
    status_for,
)
from tt_stats.core.logging_config import configure_logging, get_logger
from tt_stats.matches.land import land_match_entries

if TYPE_CHECKING:
    from psycopg import Connection

logger = get_logger(__name__)

#: Keys every match row must carry so it can be unpacked and routed.
#: ``id`` / ``iD``, ``startDateLocal``, ``subEventType`` and ``fullResults``
#: stay in the raw JSON payload for dbt (ADR 0003 / ADR 0010), so their
#: absence or upstream quirks do not block landing.
REQUIRED_MATCH_KEYS = (
    "eventId",
    "documentCode",
)

#: The queue, in the order the view defines: live events first, then finished
#: events by most recent end date. ``--limit`` takes a prefix of this.
_PENDING_EVENTS_SQL = """
SELECT p.event_id, p.reason
FROM ingest.match_entries_pending AS p
ORDER BY
    (p.reason = 'live') DESC,
    GREATEST(p.start_date, p.end_date) DESC,
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


def pending_events(
    connection: Connection[Any],
    limit: int | None = None,
) -> list[tuple[int, str]]:
    """Return ``(event_id, reason)`` pairs to fetch, in queue order.

    Args:
        connection: Open database connection.
        limit: Optional cap, applied after ordering, so a smoke run picks the
            live events rather than an arbitrary slice.
    """
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

        document_code = row.get("documentCode")
        if not isinstance(document_code, str) or not document_code.strip():
            problems.append(f"row {index} has no non-empty documentCode")

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
    return await collect_in_chunks(
        events,
        fetch=client.fetch_match_entries,
        settle=lambda fetched: _settle(fetched, land=land, record=record),
        on_chunk=_log_chunk,
        progress=progress,
        chunk_size=chunk_size,
        reasons=reasons,
    )


def _settle(
    fetched: FetchedItem[int],
    *,
    land: LandingFn | None,
    record: RecordRunFn | None,
) -> MatchEntriesSummary:
    """Validate, land and record one fetched event."""
    if fetched.response is None:
        summary = MatchEntriesSummary(
            event_id=fetched.key,
            status_code=fetched.status_code,
            received_count=0,
            payload_sha256=None,
            fetched_at=fetched.fetched_at,
            error=fetched.error or "request failed",
            reason=fetched.reason,
        )
        _log_summary(summary)
        _record_run(summary, record, started_at=fetched.started_at)
        return summary

    response = fetched.response
    digest = hashlib.sha256(response.content).hexdigest()

    received_count = 0
    error_message: str | None = None

    try:
        rows = extract_rows(response.data)
        received_count = len(rows)
        problems = validate_rows(rows, event_id=fetched.key)
        if problems:
            error_message = "row validation failed: " + "; ".join(problems[:5])
    except PayloadShapeError as error:
        error_message = f"payload shape error: {error}"

    summary = MatchEntriesSummary(
        event_id=fetched.key,
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
    _record_run(summary, record, started_at=fetched.started_at)
    return summary


def _log_chunk(
    index: int,
    total: int,
    settled: Sequence[MatchEntriesSummary],
    started_at: datetime,
) -> None:
    """Log one checkpoint per chunk, rather than one line per event."""
    log_chunk_progress(
        logger,
        index,
        total,
        settled,
        started_at,
        event="match_entries_chunk",
        unit="event",
        found_label="matches",
    )


def _record_run(
    summary: MatchEntriesSummary,
    record: RecordRunFn | None,
    *,
    started_at: datetime,
) -> None:
    """Write the attempt to ``ingest.ingest_runs``, never breaking the collect."""
    safe_record_run(
        record,
        RunRecord(
            source=SOURCE_WTT_MATCH_ENTRIES,
            entity_key=str(summary.event_id),
            status=status_for(summary.error),
            started_at=started_at,
            http_status=summary.status_code,
            received_count=summary.received_count,
            payload_sha256=summary.payload_sha256,
            error=summary.error,
        ),
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
    connection: Connection[Any],
    chunk_size: int,
    reasons: Mapping[int, str] | None,
) -> list[MatchEntriesSummary]:
    async with WttClient() as client:
        return await collect_match_entries(
            events,
            client=client,
            progress=progress,
            land=lambda summary, payload: land_match_entries(
                connection, summary, payload
            ),
            record=lambda run: record_run(connection, run),
            chunk_size=chunk_size,
            reasons=reasons,
        )


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
    add_queue_arguments(parser, unit="event")
    args = parser.parse_args(argv)
    validate_queue_arguments(parser, args)

    configure_logging()
    settings = get_settings()
    quiet_http_logs(settings)

    chunk_size = args.chunk_size or settings.chunk_size
    started_at = datetime.now(tz=UTC)

    with get_connection(autocommit=True) as connection:
        reasons: dict[int, str] = {}
        if args.event_ids is not None:
            events = list(args.event_ids)
            logger.info("match_entries_manual", extra={"events": len(events)})
        else:
            queue = pending_events(connection, args.limit)
            events = [event_id for event_id, _ in queue]
            reasons = dict(queue)
            logger.info(
                "match_entries_queue",
                extra={"events": len(events), "reasons": reason_counts(queue)},
            )

        if not events:
            logger.info("match_entries_nothing_pending")
            print("Nothing pending: every event is already fetched.")  # noqa: T201
            return 0

        chunks_total = chunk_count(len(events), chunk_size)
        logger.info(
            "match_entries_started",
            extra={
                "events": len(events),
                "chunk_size": chunk_size,
                "chunks": chunks_total,
                "concurrency": settings.max_concurrent_requests,
            },
        )

        results = run_with_progress(
            len(events),
            description="Collecting match entries",
            unit="event",
            chunk_size=chunk_size,
            settings=settings,
            message=lambda summary: progress_message(
                summary, key=f"event={summary.event_id}", count_label="matches"
            ),
            run=lambda advance: _run(
                events,
                advance,
                connection=connection,
                chunk_size=chunk_size,
                reasons=reasons or None,
            ),
        )

    return report_collect_run(
        results,
        started_at=started_at,
        chunks_total=chunks_total,
        event="match_entries",
        unit="event",
        found_label="matches",
    )


if __name__ == "__main__":
    raise SystemExit(main())

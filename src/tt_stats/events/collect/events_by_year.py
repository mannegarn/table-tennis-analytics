"""Fetch, validate and land one WTT event-calendar payload per year.

The payload is upserted into ``ingest.events_by_year_raw``; the database row is
the source of truth. No payload files are written. Every attempt, successful or
not, is also recorded in ``ingest.ingest_runs`` so the history survives the
terminal closing.

Two things changed once the calendar was backfilled to 1926 (96+ years rather
than six):

* **The queue is derived, not computed.** ``ingest.events_years_pending`` answers
  which years deserve a request: the current year and the next always, any other
  year only while its payload is missing (ADR 0005). A past year that landed is
  never fetched again.
* **Fetching is chunked and concurrent.** Each chunk is gathered, then landed
  sequentially. The chunk bounds how much unlanded work one crash can lose and
  sets the progress cadence; the client semaphore, not the chunk, bounds the
  requests in flight.

A year with no tournaments is a **success**, not a failure: the API returns the
normal envelope with an empty ``rows`` list, 228 bytes of it. Landing that
records "asked, nothing there" and the queue stops asking.
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
    SOURCE_WTT_EVENTCALENDAR,
    RunRecord,
    record_run,
    safe_record_run,
    status_for,
)
from tt_stats.core.logging_config import configure_logging, get_logger
from tt_stats.events.land import land_events_by_year

if TYPE_CHECKING:
    from psycopg import Connection

logger = get_logger(__name__)

DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

#: Which years deserve a request, in priority order. The view already knows the
#: rules; the collector only applies its configured floor. The ORDER BY is
#: repeated here because filtering a view does not guarantee its internal order.
_PENDING_YEARS_SQL = """
WITH bounds AS (
    SELECT EXTRACT(
        YEAR FROM (current_timestamp AT TIME ZONE 'UTC')
    )::int AS current_year
)
SELECT p.year, p.reason
FROM ingest.events_years_pending AS p
CROSS JOIN bounds AS b
WHERE p.year >= %(start_year)s
ORDER BY
    (p.year = b.current_year) DESC,
    (p.year = b.current_year + 1) DESC,
    p.year DESC;
"""

REQUIRED_EVENT_KEYS = (
    "EventId",
    "EventName",
    "EventType",
    "EventTypeId",
    "Country",
    "ContinentCode",
    "Subcontinent",
    "StartDateTime",
    "EndDateTime",
)


class PayloadShapeError(ValueError):
    """The response does not match the documented event-calendar envelope."""


LandingFn = Callable[["YearPayloadSummary", Any], None]
RecordRunFn = Callable[[RunRecord], None]
# Called after each year settles, so a caller can drive its own progress bar.
ProgressFn = Callable[["YearPayloadSummary"], None]


@dataclass(frozen=True, slots=True)
class YearPayloadSummary:
    """What happened for one year, including the evidence for a failure."""

    year: int
    status_code: int | None
    # WTT's own numbers, lifted from the payload and kept for audit:
    source_original_count: int | None
    source_final_count: int | None
    # Ours: rows actually parsed from payload -> 0 -> rows.
    received_count: int
    payload_sha256: str | None
    fetched_at: datetime
    error: str | None
    # Why the queue asked for this year: current, next or backfill. None when
    # the year was named explicitly on the command line.
    reason: str | None = None


def pending_years(
    connection: Connection[Any],
    start_year: int,
) -> list[tuple[int, str]]:
    """Return ``(year, reason)`` pairs the collector should request, in order.

    ``start_year`` is the backfill floor from settings. Years already landed and
    now in the past are excluded by the view, so a warm database yields only the
    current year and the next.
    """
    rows = connection.execute(_PENDING_YEARS_SQL, {"start_year": start_year}).fetchall()
    return [(int(row["year"]), str(row["reason"])) for row in rows]


def _envelope(payload: object) -> dict[str, Any]:
    """Return ``payload[0]`` as a JSON object.

    Raises:
        PayloadShapeError: If the payload is empty or ``payload[0]`` is not an
            object.
    """
    if not isinstance(payload, list) or not payload:
        raise PayloadShapeError("payload must be a non-empty list")

    items = cast("list[object]", payload)
    first = items[0]
    if not isinstance(first, dict):
        raise PayloadShapeError("payload[0] must be an object")

    return cast("dict[str, Any]", first)


def extract_rows(payload: object) -> list[dict[str, Any]]:
    """Return the event rows from the ``[0]["rows"]`` envelope.

    An empty list is valid and means "this year has no tournaments". The check
    that matters is that ``rows`` exists and is a list: a renamed key would
    otherwise land a year with zero events and look identical to a quiet year.

    Raises:
        PayloadShapeError: If the envelope or any row has the wrong shape.
    """
    raw_rows: object = _envelope(payload).get("rows")
    if not isinstance(raw_rows, list):
        raise PayloadShapeError("'rows' must be a list")

    rows: list[dict[str, Any]] = []
    for index, row in enumerate(cast("list[object]", raw_rows)):
        if not isinstance(row, dict):
            raise PayloadShapeError(f"row {index} is not an object")
        rows.append(cast("dict[str, Any]", row))

    return rows


def extract_counts(payload: object) -> tuple[int | None, int | None]:
    """Return ``(Count, Original_Count)`` as integers when available."""
    envelope = _envelope(payload)
    return _as_int(envelope.get("Count")), _as_int(envelope.get("Original_Count"))


def validate_rows(rows: Sequence[dict[str, Any]]) -> list[str]:
    """Return a list of contract problems, empty when every row is valid."""
    problems: list[str] = []

    for index, row in enumerate(rows):
        missing = [key for key in REQUIRED_EVENT_KEYS if key not in row]
        if missing:
            problems.append(f"row {index} missing keys: {', '.join(missing)}")
            continue

        event_id = row.get("EventId")
        if not isinstance(event_id, int) or isinstance(event_id, bool):
            problems.append(f"row {index} EventId is not an int")

        for key in ("StartDateTime", "EndDateTime"):
            try:
                datetime.strptime(str(row.get(key)), DATE_FORMAT)
            except ValueError:
                problems.append(f"row {index} {key} is not in {DATE_FORMAT} format")

    return problems


async def collect_events_by_year(
    years: Sequence[int],
    *,
    client: WttClient,
    progress: ProgressFn | None = None,
    land: LandingFn | None = None,
    record: RecordRunFn | None = None,
    chunk_size: int | None = None,
    reasons: Mapping[int, str] | None = None,
) -> list[YearPayloadSummary]:
    """Fetch and land every requested year, one chunk at a time.

    Each chunk is fetched concurrently and then landed sequentially: landing is
    synchronous psycopg work, and keeping it in one place preserves the one
    transaction per year that the invariants rely on, plus deterministic log
    order. One failed year never stops the others.

    ``record`` receives every settled attempt, so run history is written from
    inside the loop rather than reconstructed afterwards. ``progress`` is called
    with each settled summary; drawing is the caller's business, which keeps
    this function free of terminal concerns.

    ``chunk_size`` of ``None`` means "one chunk", which is what the small
    explicit ``--years`` runs want.
    """
    return await collect_in_chunks(
        years,
        fetch=client.fetch_events_by_year,
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
) -> YearPayloadSummary:
    """Validate, land and record one fetched year."""
    if fetched.response is None:
        summary = _failed_summary(
            year=fetched.key,
            fetched_at=fetched.fetched_at,
            status_code=fetched.status_code,
            message=fetched.error or "request failed",
            reason=fetched.reason,
        )
        _record_run(summary, record, started_at=fetched.started_at)
        return summary

    response = fetched.response
    digest = hashlib.sha256(response.content).hexdigest()

    source_final_count: int | None = None
    source_original_count: int | None = None
    received_count = 0
    error_message: str | None = None

    try:
        rows = extract_rows(response.data)
        source_final_count, source_original_count = extract_counts(response.data)
        received_count = len(rows)

        problems = validate_rows(rows)
        if problems:
            error_message = "row validation failed: " + "; ".join(problems[:5])
        elif source_final_count is None:
            error_message = "payload has no Count"
        elif source_final_count != received_count:
            error_message = (
                f"count mismatch: source_final_count={source_final_count} "
                f"received_count={received_count}"
            )
    except PayloadShapeError as error:
        error_message = f"payload shape error: {error}"

    summary = YearPayloadSummary(
        year=fetched.key,
        status_code=response.status_code,
        source_original_count=source_original_count,
        source_final_count=source_final_count,
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
            summary = replace(
                summary,
                error=f"landing failed: {error}",
            )

    _log_summary(summary)
    _record_run(summary, record, started_at=fetched.started_at)
    return summary


def _failed_summary(
    *,
    year: int,
    fetched_at: datetime,
    status_code: int | None,
    message: str,
    reason: str | None = None,
) -> YearPayloadSummary:
    summary = YearPayloadSummary(
        year=year,
        status_code=status_code,
        source_original_count=None,
        source_final_count=None,
        received_count=0,
        payload_sha256=None,
        fetched_at=fetched_at,
        error=message,
        reason=reason,
    )
    _log_summary(summary)
    return summary


def _log_chunk(
    index: int,
    total: int,
    settled: Sequence[YearPayloadSummary],
    started_at: datetime,
) -> None:
    """Log one checkpoint per chunk, rather than one line per year.

    A full backfill is ~100 years, so per-year records are tolerable but noisy.
    One line per chunk keeps progress, throughput and failures visible, and
    matches the shape of the match-entries collector.
    """
    received = sum(summary.received_count for summary in settled)
    empty = sum(
        1 for summary in settled if not summary.error and summary.received_count == 0
    )
    failed = sum(1 for summary in settled if summary.error)
    elapsed = (datetime.now(tz=UTC) - started_at).total_seconds()

    logger.info(
        "events_chunk",
        extra={
            "chunk": index,
            "chunks": total,
            "years": len(settled),
            "events": received,
            "empty": empty,
            "failed": failed,
            "chunk_seconds": round(elapsed, 3),
            "chunk_rate_per_second": round(len(settled) / elapsed, 2) if elapsed else 0,
        },
    )


def _record_run(
    summary: YearPayloadSummary,
    record: RecordRunFn | None,
    *,
    started_at: datetime,
) -> None:
    """Write the attempt to ``ingest.ingest_runs``, never breaking the collect."""
    safe_record_run(
        record,
        RunRecord(
            source=SOURCE_WTT_EVENTCALENDAR,
            entity_key=str(summary.year),
            status=status_for(summary.error),
            started_at=started_at,
            http_status=summary.status_code,
            received_count=summary.received_count,
            payload_sha256=summary.payload_sha256,
            error=summary.error,
        ),
    )


def _log_summary(summary: YearPayloadSummary) -> None:
    extra: dict[str, Any] = {
        "year": summary.year,
        "reason": summary.reason,
        "status": summary.status_code,
        "received_count": summary.received_count,
        "source_final_count": summary.source_final_count,
        "source_original_count": summary.source_original_count,
    }
    if summary.error:
        logger.error(
            "events_year_incomplete",
            extra={**extra, "error": summary.error},
        )
    elif summary.received_count == 0:
        # A quiet year is a finding, not a fault: it is recorded once and never
        # requested again. DEBUG, because the chunk line already counts them.
        logger.debug("events_year_empty", extra=extra)
    else:
        logger.debug("events_year_saved", extra=extra)


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


async def _run(
    years: Sequence[int],
    progress: ProgressFn | None,
    *,
    connection: Connection[Any],
    chunk_size: int,
    reasons: Mapping[int, str] | None,
) -> list[YearPayloadSummary]:
    async with WttClient() as client:
        return await collect_events_by_year(
            years,
            client=client,
            progress=progress,
            land=lambda summary, payload: land_events_by_year(
                connection, summary, payload
            ),
            record=lambda run: record_run(connection, run),
            chunk_size=chunk_size,
            reasons=reasons,
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-events-collect``."""
    parser = argparse.ArgumentParser(
        description=(
            "Fetch WTT event calendars into ingest.events_by_year_raw. Without "
            "--years, the pending view decides: the current year, the next "
            "year, and any year whose payload is missing."
        )
    )
    parser.add_argument(
        "--years",
        nargs="+",
        type=int,
        default=None,
        help="Explicit years to fetch, bypassing the pending view.",
    )
    add_queue_arguments(parser, unit="year")
    args = parser.parse_args(argv)
    validate_queue_arguments(parser, args)

    configure_logging()
    settings = get_settings()
    quiet_http_logs(settings)

    chunk_size = args.chunk_size or settings.chunk_size
    started_at = datetime.now(tz=UTC)

    with get_connection(autocommit=True) as connection:
        reasons: dict[int, str] = {}
        if args.years is not None:
            years = list(args.years)
            logger.info("events_collect_manual", extra={"years": len(years)})
        else:
            queue = pending_years(connection, settings.events_start_year)
            if args.limit is not None:
                queue = queue[: args.limit]
            years = [year for year, _ in queue]
            reasons = dict(queue)
            logger.info(
                "events_collect_queue",
                extra={
                    "years": len(years),
                    "reasons": reason_counts(queue),
                    "start_year": settings.events_start_year,
                },
            )

        if not years:
            logger.info("events_collect_nothing_pending")
            print("Nothing pending: every year is already fetched.")  # noqa: T201
            return 0

        chunks_total = chunk_count(len(years), chunk_size)
        logger.info(
            "events_collect_started",
            extra={
                "years": len(years),
                "chunk_size": chunk_size,
                "chunks": chunks_total,
                "concurrency": settings.max_concurrent_requests,
            },
        )

        results = run_with_progress(
            len(years),
            description="Collecting calendar",
            unit="year",
            chunk_size=chunk_size,
            settings=settings,
            message=lambda summary: progress_message(
                summary, key=f"year={summary.year}", count_label="received"
            ),
            run=lambda advance: _run(
                years,
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
        event="events_collect",
        unit="year",
        found_label="events",
    )


if __name__ == "__main__":
    raise SystemExit(main())

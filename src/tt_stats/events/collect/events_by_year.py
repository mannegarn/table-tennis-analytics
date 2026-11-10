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
    SOURCE_WTT_EVENTCALENDAR,
    RunRecord,
    record_run,
    status_for,
)
from tt_stats.core.logging_config import configure_logging, get_logger
from tt_stats.core.progress import RunTiming, plural, progress_bar
from tt_stats.events.land import land_events_by_year

logger = get_logger(__name__)

DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

#: Which years deserve a request, in priority order. The view already knows the
#: rules; the collector only applies its configured floor. The ORDER BY is
#: repeated here because filtering a view does not guarantee its internal order.
_PENDING_YEARS_SQL = """
WITH bounds AS (
    SELECT EXTRACT(YEAR FROM current_date)::int AS current_year
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


@dataclass(frozen=True, slots=True)
class _Fetched:
    """One settled network attempt, before any validation or landing.

    Splitting fetch from settle is what makes chunking possible: a whole chunk
    is fetched concurrently, then each result is validated and landed in order
    on the calling coroutine.
    """

    year: int
    fetched_at: datetime
    response: WttResponse | None
    error: str | None
    status_code: int | None
    reason: str | None


def pending_years(start_year: int) -> list[tuple[int, str]]:
    """Return ``(year, reason)`` pairs the collector should request, in order.

    ``start_year`` is the backfill floor from settings. Years already landed and
    now in the past are excluded by the view, so a warm database yields only the
    current year and the next.
    """
    with get_connection() as connection:
        rows = connection.execute(
            _PENDING_YEARS_SQL, {"start_year": start_year}
        ).fetchall()
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
    summaries: list[YearPayloadSummary] = []
    size = chunk_size if chunk_size is not None else max(len(years), 1)
    reason_for = reasons or {}

    batches = chunks(list(years), size)

    for index, chunk in enumerate(batches, start=1):
        chunk_started = datetime.now(tz=UTC)
        outcomes = await asyncio.gather(
            *(
                _fetch_one(year, client=client, reason=reason_for.get(year))
                for year in chunk
            ),
            # A bug in one coroutine must not cancel its siblings; it becomes a
            # failed year with the exception as the reason.
            return_exceptions=True,
        )

        settled: list[YearPayloadSummary] = []
        for year, outcome in zip(chunk, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                fetched = _Fetched(
                    year=year,
                    fetched_at=datetime.now(tz=UTC),
                    response=None,
                    error=f"{type(outcome).__name__}: {outcome}",
                    status_code=None,
                    reason=reason_for.get(year),
                )
            else:
                fetched = outcome

            summary = _settle(fetched, land=land, record=record)
            settled.append(summary)
            summaries.append(summary)
            if progress is not None:
                progress(summary)

        _log_chunk(index, len(batches), settled, started_at=chunk_started)

    return summaries


async def _fetch_one(
    year: int,
    *,
    client: WttClient,
    reason: str | None,
) -> _Fetched:
    """Fetch one year and describe the outcome without raising."""
    fetched_at = datetime.now(tz=UTC)

    try:
        response = await client.fetch_events_by_year(year)
    except WttRequestError as error:
        return _Fetched(
            year=year,
            fetched_at=fetched_at,
            response=None,
            error=str(error),
            status_code=error.status_code,
            reason=reason,
        )
    except WttPayloadError as error:
        return _Fetched(
            year=year,
            fetched_at=fetched_at,
            response=None,
            error=str(error),
            status_code=None,
            reason=reason,
        )

    return _Fetched(
        year=year,
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
) -> YearPayloadSummary:
    """Validate, land and record one fetched year."""
    if fetched.response is None:
        summary = _failed_summary(
            year=fetched.year,
            fetched_at=fetched.fetched_at,
            status_code=fetched.status_code,
            message=fetched.error or "request failed",
            reason=fetched.reason,
        )
        _record_run(summary, record)
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
        year=fetched.year,
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
    _record_run(summary, record)
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
    *,
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


def _progress_message(summary: YearPayloadSummary) -> str:
    if summary.error:
        state = "failed"
    elif summary.received_count == 0:
        state = "empty"
    else:
        state = "ok"
    reason = f"{summary.reason} " if summary.reason else ""
    return f"{reason}year={summary.year} | received={summary.received_count} | {state}"


def _record_run(summary: YearPayloadSummary, record: RecordRunFn | None) -> None:
    """Write the attempt to ``ingest.ingest_runs``, never breaking the collect."""
    if record is None:
        return

    try:
        record(
            RunRecord(
                source=SOURCE_WTT_EVENTCALENDAR,
                entity_key=str(summary.year),
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
            extra={"year": summary.year, "error": str(error)},
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
    chunk_size: int,
    reasons: Mapping[int, str] | None,
) -> list[YearPayloadSummary]:
    async with WttClient() as client:
        return await collect_events_by_year(
            years,
            client=client,
            progress=progress,
            land=land_events_by_year,
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
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Fetch at most this many years from the queue, in queue order.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=None,
        help="Years fetched per batch. Defaults to Settings.chunk_size.",
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
    if args.years is not None:
        years = list(args.years)
        logger.info("events_collect_manual", extra={"years": len(years)})
    else:
        queue = pending_years(settings.events_start_year)
        if args.limit is not None:
            queue = queue[: args.limit]
        years = [year for year, _ in queue]
        reasons = dict(queue)
        logger.info(
            "events_collect_queue",
            extra={
                "years": len(years),
                "reasons": _reason_counts(queue),
                "start_year": settings.events_start_year,
            },
        )

    if not years:
        logger.info("events_collect_nothing_pending")
        print("Nothing pending: every year is already fetched.")  # noqa: T201
        return 0

    if settings.log_level.upper() != "DEBUG":
        # One line per HTTP request would bury the audit trail.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    chunk_size = args.chunk_size or settings.chunk_size
    chunks_total = -(-len(years) // chunk_size)
    started_at = datetime.now(tz=UTC)
    logger.info(
        "events_collect_started",
        extra={
            "years": len(years),
            "chunk_size": chunk_size,
            "chunks": chunks_total,
            "concurrency": settings.max_concurrent_requests,
        },
    )

    # The bar owns one stdout line and rewrites it. Routine records are INFO,
    # so the WARNING-level console handler cannot push it down a line; they
    # still land in logs/tt_stats.log.
    with progress_bar(
        len(years),
        description=(
            f"Collecting calendar "
            f"({chunk_size}/chunk, {settings.max_concurrent_requests} workers)"
        ),
        unit="year",
        disable=settings.log_json,
    ) as bar:

        def advance(summary: YearPayloadSummary) -> None:
            bar.set_postfix_str(_progress_message(summary), refresh=False)
            bar.update(1)

        results = asyncio.run(
            _run(
                years,
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
    events_found = sum(result.received_count for result in results)
    tally: dict[str, Any] = {
        "years": len(results),
        "chunks": chunks_total,
        "events": events_found,
        "failed": len(failed),
        "empty": len(empty),
        "elapsed_seconds": round(timing.elapsed.total_seconds(), 3),
        "rate_per_second": round(timing.rate_per_second(len(results)), 3),
    }
    if failed:
        logger.error("events_collect_incomplete", extra=tally)
    else:
        logger.info("events_collect_complete", extra=tally)

    # Deliberate stdout write: terminal logging sits at WARNING, so the record
    # above never reaches the console. This is the run summary, not a log line.
    print(  # noqa: T201
        f"{timing.describe(len(results), unit='year')}, "
        f"{chunks_total:,} {plural(chunks_total, 'chunk')}: "
        f"{events_found:,} events found, "
        f"{len(empty):,} {plural(len(empty), 'year')} with no events, "
        f"{len(failed):,} failed"
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

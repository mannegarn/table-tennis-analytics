"""Fetch, validate and land one WTT event-calendar payload per year.

The payload is upserted into ``ingest.events_by_year_raw``; the database row is
the source of truth. No payload files are written. Every attempt, successful or
not, is also recorded in ``ingest.ingest_runs`` so the history survives the
terminal closing.
"""

import argparse
import asyncio
import hashlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, cast

from tt_stats.core.clients.wtt_api import (
    WttClient,
    WttPayloadError,
    WttRequestError,
)
from tt_stats.core.config import get_settings
from tt_stats.core.ingest_runs import (
    SOURCE_WTT_EVENTCALENDAR,
    RunRecord,
    record_run,
    status_for,
)
from tt_stats.core.logging_config import configure_logging, get_logger
from tt_stats.core.progress import progress_bar
from tt_stats.events.land import land_events_by_year

logger = get_logger(__name__)

DEFAULT_START_YEAR = 2021
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


# Default range: 2021 through the current year. Change this when the backfill
# window or the "current year only" policy changes.
def default_years() -> list[int]:
    return list(range(DEFAULT_START_YEAR, datetime.now(tz=UTC).year + 1))


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

    Raises:
        PayloadShapeError: If the envelope or any row has the wrong shape.
    """
    raw_rows: object = _envelope(payload).get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise PayloadShapeError("'rows' must be a non-empty list")

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
) -> list[YearPayloadSummary]:
    """Fetch and land every requested year.

    One failed year does not stop the others. The caller decides what a
    non-empty error list means for the run.

    ``record`` receives every settled attempt, so run history is written from
    inside the loop rather than reconstructed afterwards. ``progress`` is called
    with each settled summary; drawing is the caller's business, which keeps
    this function free of terminal concerns.
    """
    summaries: list[YearPayloadSummary] = []

    # Intentionally sequential: one request per year, a small fixed list. The
    # match fan-out is where concurrency and chunking belong.
    for year in years:
        summary = await _collect_one(
            year,
            client=client,
            land=land,
            record=record,
        )
        summaries.append(summary)
        if progress is not None:
            progress(summary)

    return summaries


async def _collect_one(
    year: int,
    *,
    client: WttClient,
    land: LandingFn | None = None,
    record: RecordRunFn | None = None,
) -> YearPayloadSummary:
    fetched_at = datetime.now(tz=UTC)

    try:
        response = await client.fetch_events_by_year(year)
    except WttRequestError as error:
        summary = _failed_summary(
            year=year,
            fetched_at=fetched_at,
            status_code=error.status_code,
            message=str(error),
        )
        _record_run(summary, record)
        return summary
    except WttPayloadError as error:
        summary = _failed_summary(
            year=year,
            fetched_at=fetched_at,
            status_code=None,
            message=str(error),
        )
        _record_run(summary, record)
        return summary

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
        year=year,
        status_code=response.status_code,
        source_original_count=source_original_count,
        source_final_count=source_final_count,
        received_count=received_count,
        payload_sha256=digest,
        fetched_at=fetched_at,
        error=error_message,
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
    )
    _log_summary(summary)
    return summary


def _progress_message(summary: YearPayloadSummary) -> str:
    state = "failed" if summary.error else "ok"
    source = (
        f" | source={summary.source_final_count}"
        if summary.source_final_count is not None
        else ""
    )
    return f"year={summary.year} | received={summary.received_count}{source} | {state}"


def _record_run(summary: YearPayloadSummary, record: RecordRunFn | None) -> None:
    """Write the attempt to ``ingest.ingest_runs``, never breaking the scrape."""
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
    else:
        logger.info("events_year_saved", extra=extra)


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
) -> list[YearPayloadSummary]:
    async with WttClient() as client:
        return await collect_events_by_year(
            years,
            client=client,
            progress=progress,
            land=land_events_by_year,
            record=record_run,
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-events-scrape``."""
    configure_logging()

    parser = argparse.ArgumentParser(
        description="Fetch WTT event calendars into ingest.events_by_year_raw."
    )
    parser.add_argument(
        "--years",
        nargs="+",
        type=int,
        default=None,
    )
    args = parser.parse_args(argv)
    years = args.years if args.years is not None else default_years()

    settings = get_settings()
    if settings.log_level.upper() != "DEBUG":
        # One line per HTTP request would bury the audit trail.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    logger.info("events_scrape_started", extra={"years": len(years)})

    # The bar owns one stdout line and rewrites it. Routine records are INFO,
    # so the WARNING-level console handler cannot push it down a line; they
    # still land in logs/tt_stats.log.
    with progress_bar(
        len(years),
        description="Scraping calendar",
        unit="year",
        disable=settings.log_json,
    ) as bar:

        def advance(summary: YearPayloadSummary) -> None:
            bar.set_postfix_str(_progress_message(summary), refresh=False)
            bar.update(1)

        results = asyncio.run(_run(years, advance))

    failed = [result for result in results if result.error]
    if failed:
        logger.error(
            "events_scrape_incomplete",
            extra={"years": len(results), "failed": len(failed)},
        )
    else:
        logger.info(
            "events_scrape_complete",
            extra={"years": len(results)},
        )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

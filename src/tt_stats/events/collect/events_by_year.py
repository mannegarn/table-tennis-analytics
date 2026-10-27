"""Fetch, validate and land one WTT event-calendar payload per year.

The payload is saved to disk as a local debug copy and upserted into
``ingest.events_by_year_raw`` in the same run. The raw table is the source of
truth; the file is there so a failed or changed response can be inspected.
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
from tt_stats.core.logging_config import configure_logging, get_logger
from tt_stats.core.progress import (
    NullProgress,
    ProgressReporter,
    make_progress,
)
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


@dataclass(frozen=True, slots=True)
class YearPayloadSummary:
    """What happened for one year, including the evidence for a failure."""

    year: int
    status_code: int | None
    api_count: int | None
    original_count: int | None
    row_count: int
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
    progress: ProgressReporter | None = None,
    land: LandingFn | None = None,
) -> list[YearPayloadSummary]:
    """Fetch and land every requested year.

    One failed year does not stop the others. The caller decides what a
    non-empty error list means for the run.
    """
    summaries: list[YearPayloadSummary] = []
    reporter = progress or NullProgress()

    reporter.start(
        "Starting events scrape",
        {
            "Total Years": str(len(years)),
            "Output": "ingest.events_by_year_raw",
        },
    )
    try:
        # Intentionally sequential: one request per year, a small fixed list.
        # The match fan-out is where concurrency and chunking belong.
        for year in years:
            summary = await _collect_one(
                year,
                client=client,
                log_summary=reporter.logs_records,
                land=land,
            )
            summaries.append(summary)
            reporter.advance(_progress_message(summary))
    finally:
        reporter.close()
        failed = sum(1 for summary in summaries if summary.error)
        reporter.finish(
            "Events scrape complete",
            {"Total Years": str(len(summaries)), "Failed": str(failed)},
        )

    return summaries


async def _collect_one(
    year: int,
    *,
    client: WttClient,
    log_summary: bool = True,
    land: LandingFn | None = None,
) -> YearPayloadSummary:
    fetched_at = datetime.now(tz=UTC)

    try:
        response = await client.fetch_events_by_year(year)
    except WttRequestError as error:
        return _failed_summary(
            year=year,
            fetched_at=fetched_at,
            status_code=error.status_code,
            message=str(error),
            log_summary=log_summary,
        )
    except WttPayloadError as error:
        return _failed_summary(
            year=year,
            fetched_at=fetched_at,
            status_code=None,
            message=str(error),
            log_summary=log_summary,
        )

    digest = hashlib.sha256(response.content).hexdigest()

    api_count: int | None = None
    original_count: int | None = None
    row_count = 0
    error_message: str | None = None

    try:
        rows = extract_rows(response.data)
        api_count, original_count = extract_counts(response.data)
        row_count = len(rows)

        problems = validate_rows(rows)
        if problems:
            error_message = "row validation failed: " + "; ".join(problems[:5])
        elif api_count is None:
            error_message = "payload has no Count"
        elif api_count != row_count:
            error_message = f"count mismatch: Count={api_count} rows={row_count}"
    except PayloadShapeError as error:
        error_message = f"payload shape error: {error}"

    summary = YearPayloadSummary(
        year=year,
        status_code=response.status_code,
        api_count=api_count,
        original_count=original_count,
        row_count=row_count,
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

    if log_summary:
        _log_summary(summary)
    return summary


def _failed_summary(
    *,
    year: int,
    fetched_at: datetime,
    status_code: int | None,
    message: str,
    log_summary: bool,
) -> YearPayloadSummary:
    summary = YearPayloadSummary(
        year=year,
        status_code=status_code,
        api_count=None,
        original_count=None,
        row_count=0,
        payload_sha256=None,
        fetched_at=fetched_at,
        error=message,
    )
    if log_summary:
        _log_summary(summary)
    return summary


def _progress_message(summary: YearPayloadSummary) -> str:
    state = "failed" if summary.error else "ok"
    api = f" | api={summary.api_count}" if summary.api_count is not None else ""
    return f"year={summary.year} | saved={summary.row_count}{api} | {state}"


def _log_summary(summary: YearPayloadSummary) -> None:
    extra: dict[str, Any] = {
        "year": summary.year,
        "status": summary.status_code,
        "rows": summary.row_count,
        "api_count": summary.api_count,
        "original_count": summary.original_count,
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
    progress: ProgressReporter,
) -> list[YearPayloadSummary]:
    async with WttClient() as client:
        return await collect_events_by_year(
            years,
            client=client,
            progress=progress,
            land=land_events_by_year,
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
    if not settings.log_json and settings.log_level.upper() != "DEBUG":
        # One line per HTTP request drowns out the progress display.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
    progress = make_progress(
        len(years),
        json_output=settings.log_json,
    )
    if not progress.prints_summary:
        logger.info(
            "Starting events scrape",
            extra={"years": len(years)},
        )
    results = asyncio.run(_run(years, progress))

    failed = [result for result in results if result.error]
    if not progress.prints_summary:
        logger.info(
            "Events scrape complete",
            extra={
                "years": len(results),
                "failed": len(failed),
            },
        )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

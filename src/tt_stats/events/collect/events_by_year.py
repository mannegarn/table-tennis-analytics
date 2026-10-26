"""Fetch and save one WTT event-calendar payload per year.

This is deliberately file-only. It proves the endpoint, headers, retry policy
and payload shape before any table exists. The saved payload is the source of
truth that later unpack work will be tested against.
"""

import argparse
import asyncio
import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from tt_stats.core.clients.wtt_api import (
    WttClient,
    WttPayloadError,
    WttRequestError,
)
from tt_stats.core.config import get_settings
from tt_stats.core.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

DEFAULT_YEARS = tuple(range(2021, 2027))
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"
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
    payload_path: Path | None
    error: str | None


def extract_rows(payload: object) -> list[dict[str, Any]]:
    """Return the event rows from the ``[0]["rows"]`` envelope.

    Raises:
        PayloadShapeError: If the envelope or any row has the wrong shape.
    """
    if not isinstance(payload, list) or not payload:
        raise PayloadShapeError("payload must be a non-empty list")

    envelope = payload[0]
    if not isinstance(envelope, dict):
        raise PayloadShapeError("payload[0] must be an object")

    rows = envelope.get("rows")
    if not isinstance(rows, list) or not rows:
        raise PayloadShapeError("'rows' must be a non-empty list")

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise PayloadShapeError(f"row {index} is not an object")

    return cast("list[dict[str, Any]]", rows)


def extract_counts(payload: object) -> tuple[int | None, int | None]:
    """Return ``(Count, Original_Count)`` as integers when available."""
    if not isinstance(payload, list) or not payload:
        raise PayloadShapeError("payload must be a non-empty list")

    envelope = payload[0]
    if not isinstance(envelope, dict):
        raise PayloadShapeError("payload[0] must be an object")

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
    output_dir: Path,
) -> list[YearPayloadSummary]:
    """Fetch every requested year, saving payloads and metadata as it goes.

    One failed year does not stop the others. The caller decides what a
    non-empty error list means for the run.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[YearPayloadSummary] = []

    for year in years:
        summaries.append(await _collect_one(year, client=client, output_dir=output_dir))

    return summaries


async def _collect_one(
    year: int,
    *,
    client: WttClient,
    output_dir: Path,
) -> YearPayloadSummary:
    fetched_at = datetime.now(tz=UTC)

    try:
        response = await client.fetch_events_by_year(year)
    except WttRequestError as error:
        return _failed_summary(
            year=year,
            fetched_at=fetched_at,
            output_dir=output_dir,
            status_code=error.status_code,
            message=str(error),
        )
    except WttPayloadError as error:
        return _failed_summary(
            year=year,
            fetched_at=fetched_at,
            output_dir=output_dir,
            status_code=None,
            message=str(error),
        )

    payload_path = output_dir / f"events_{year}.json"
    payload_path.write_bytes(response.content)
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
        payload_path=payload_path,
        error=error_message,
    )
    _write_meta(output_dir, summary)
    _log_summary(summary)
    return summary


def _failed_summary(
    *,
    year: int,
    fetched_at: datetime,
    output_dir: Path,
    status_code: int | None,
    message: str,
) -> YearPayloadSummary:
    summary = YearPayloadSummary(
        year=year,
        status_code=status_code,
        api_count=None,
        original_count=None,
        row_count=0,
        payload_sha256=None,
        fetched_at=fetched_at,
        payload_path=None,
        error=message,
    )
    _write_meta(output_dir, summary)
    _log_summary(summary)
    return summary


def _write_meta(output_dir: Path, summary: YearPayloadSummary) -> None:
    meta_path = output_dir / f"events_{summary.year}.meta.json"
    meta_path.write_text(
        json.dumps(asdict(summary), indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _log_summary(summary: YearPayloadSummary) -> None:
    extra = {
        "year": summary.year,
        "status": summary.status_code,
        "rows": summary.row_count,
        "api_count": summary.api_count,
        "original_count": summary.original_count,
    }
    if summary.error:
        logger.error("events_year_incomplete", extra={**extra, "error": summary.error})
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


async def _run(years: Sequence[int], output_dir: Path) -> list[YearPayloadSummary]:
    async with WttClient() as client:
        return await collect_events_by_year(years, client=client, output_dir=output_dir)


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-events-scrape``."""
    configure_logging()

    parser = argparse.ArgumentParser(
        description="Fetch WTT event calendars into data/raw/events."
    )
    parser.add_argument(
        "--years",
        nargs="+",
        type=int,
        default=list(DEFAULT_YEARS),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    output_dir = args.output_dir or (settings.data_dir / "raw" / "events")
    results = asyncio.run(_run(args.years, output_dir))

    failed = [result for result in results if result.error]
    logger.info(
        "events_scrape_complete",
        extra={
            "years": len(results),
            "failed": len(failed),
            "output_dir": str(output_dir),
        },
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

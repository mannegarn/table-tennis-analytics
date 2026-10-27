"""Unit tests for the events-by-year collector. No network, no database."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from tt_stats.core.clients.wtt_api import WttClient
from tt_stats.core.config import Settings
from tt_stats.events.collect.events_by_year import (
    PayloadShapeError,
    YearPayloadSummary,
    collect_events_by_year,
    default_years,
    extract_counts,
    extract_rows,
    validate_rows,
)


class RecordingProgress:
    """Capture progress messages without drawing anything."""

    logs_records = False
    prints_summary = True

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.start_context: dict[str, str] = {}
        self.closed = False

    def start(self, title: str, context: Mapping[str, str]) -> None:
        self.start_context = dict(context)

    def advance(self, message: str) -> None:
        self.messages.append(message)

    def finish(self, title: str, context: Mapping[str, str]) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "postgres_password": "test",
        "max_retry_attempts": 1,
        "retry_wait_min_seconds": 0.001,
        "retry_wait_max_seconds": 0.001,
        "request_jitter_seconds": 0.0,
    }
    values.update(overrides)
    return Settings(**values)


def _row(event_id: int, tier: str | None = "WTT Series") -> dict[str, Any]:
    return {
        "EventId": event_id,
        "EventName": f"Event {event_id}",
        "EventType": "WTT Contender",
        "EventTypeId": 1,
        "Event_Tier_Name": tier,
        "Country": "Qatar",
        "ContinentCode": "asia",
        "Subcontinent": "gulf",
        "StartDateTime": "2023-01-10T00:00:00",
        "EndDateTime": "2023-01-15T00:00:00",
    }


def _payload(
    rows: list[dict[str, Any]],
    *,
    count: int | None = None,
    original: int | None = None,
) -> list[dict[str, Any]]:
    return [
        {
            "rows": rows,
            "Count": len(rows) if count is None else count,
            "Original_Count": str(len(rows) if original is None else original),
            "filterconfig": {},
        }
    ]


def test_extract_rows_rejects_the_wrong_envelope() -> None:
    with pytest.raises(PayloadShapeError):
        extract_rows({"rows": []})


def test_extract_counts_reads_both_values() -> None:
    api_count, original_count = extract_counts(_payload([_row(1)], original=7))

    assert api_count == 1
    assert original_count == 7


def test_validate_rows_accepts_a_complete_row() -> None:
    assert validate_rows([_row(1)]) == []


def test_validate_rows_reports_missing_keys() -> None:
    row = _row(1)
    del row["EventId"]

    problems = validate_rows([row])

    assert problems
    assert "EventId" in problems[0]


def test_default_years_runs_from_2021_to_the_current_year() -> None:
    years = default_years()
    current_year = datetime.now(tz=UTC).year

    assert years[0] == 2021
    assert years[-1] == current_year
    assert years == list(range(2021, current_year + 1))


async def test_collect_returns_a_summary() -> None:
    payload = _payload([_row(1), _row(2)])

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_events_by_year([2023], client=client)

    result = results[0]
    assert result.error is None
    assert result.status_code == 200
    assert result.row_count == 2
    assert result.api_count == 2
    assert result.original_count == 2
    assert result.payload_sha256 is not None
    assert len(result.payload_sha256) == 64


async def test_collect_reports_progress_for_every_year() -> None:
    payload = _payload([_row(1)])

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    progress = RecordingProgress()

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        await collect_events_by_year(
            [2023, 2024],
            client=client,
            progress=progress,
        )

    assert progress.start_context["Output"] == "ingest.events_by_year_raw"
    assert progress.messages == [
        "year=2023 | saved=1 | api=1 | ok",
        "year=2024 | saved=1 | api=1 | ok",
    ]
    assert progress.closed is True


async def test_collect_lands_successful_years() -> None:
    payload = _payload([_row(1)])
    landed: list[tuple[int, int]] = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    def land(summary: YearPayloadSummary, data: object) -> None:
        landed.append((summary.year, summary.row_count))

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_events_by_year(
            [2023],
            client=client,
            land=land,
        )

    assert results[0].error is None
    assert landed == [(2023, 1)]


async def test_landing_failure_marks_the_year_failed() -> None:
    payload = _payload([_row(1)])

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    def land(summary: YearPayloadSummary, data: object) -> None:
        raise RuntimeError("db down")

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_events_by_year(
            [2023],
            client=client,
            land=land,
        )

    assert results[0].error == "landing failed: db down"


async def test_count_mismatch_is_an_error() -> None:
    payload = _payload([_row(1)], count=2)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_events_by_year([2023], client=client)

    result = results[0]
    assert result.error is not None
    assert "count mismatch" in result.error


async def test_http_failure_returns_a_failed_summary() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "unavailable"})

    progress = RecordingProgress()

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_events_by_year(
            [2023],
            client=client,
            progress=progress,
        )

    result = results[0]
    assert result.error is not None
    assert result.status_code == 503
    assert progress.messages == ["year=2023 | saved=0 | failed"]

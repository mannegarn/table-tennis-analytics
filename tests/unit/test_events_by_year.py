"""Unit tests for the events-by-year collector. No network, no database."""

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from tt_stats.core.clients.wtt_api import WttClient
from tt_stats.core.config import Settings
from tt_stats.core.ingest_runs import RunRecord
from tt_stats.events.collect.events_by_year import (
    PayloadShapeError,
    YearPayloadSummary,
    collect_events_by_year,
    default_years,
    extract_counts,
    extract_rows,
    validate_rows,
)


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
    source_final_count, source_original_count = extract_counts(
        _payload([_row(1)], original=7)
    )

    assert source_final_count == 1
    assert source_original_count == 7


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
    assert result.received_count == 2
    assert result.source_final_count == 2
    assert result.source_original_count == 2
    assert result.payload_sha256 is not None
    assert len(result.payload_sha256) == 64


async def test_collect_reports_progress_for_every_year() -> None:
    payload = _payload([_row(1)])

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    reported: list[YearPayloadSummary] = []

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        await collect_events_by_year(
            [2023, 2024],
            client=client,
            progress=reported.append,
        )

    assert [summary.year for summary in reported] == [2023, 2024]
    assert [summary.received_count for summary in reported] == [1, 1]
    assert all(summary.error is None for summary in reported)


async def test_collect_lands_successful_years() -> None:
    payload = _payload([_row(1)])
    landed: list[tuple[int, int]] = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    def land(summary: YearPayloadSummary, data: object) -> None:
        landed.append((summary.year, summary.received_count))

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

    reported: list[YearPayloadSummary] = []

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_events_by_year(
            [2023],
            client=client,
            progress=reported.append,
        )

    result = results[0]
    assert result.error is not None
    assert result.status_code == 503
    assert [summary.error is not None for summary in reported] == [True]


async def test_collect_records_a_successful_run() -> None:
    payload = _payload([_row(1), _row(2)])
    recorded: list[RunRecord] = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        await collect_events_by_year([2023], client=client, record=recorded.append)

    assert len(recorded) == 1
    record = recorded[0]
    assert record.source == "wtt_eventcalendar"
    assert record.entity_key == "2023"
    assert record.status == "succeeded"
    assert record.http_status == 200
    assert record.received_count == 2
    assert record.payload_sha256 is not None
    assert record.error is None


async def test_collect_records_a_failed_run() -> None:
    recorded: list[RunRecord] = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "unavailable"})

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        await collect_events_by_year([2023], client=client, record=recorded.append)

    assert len(recorded) == 1
    record = recorded[0]
    assert record.status == "failed"
    assert record.http_status == 503
    assert record.received_count == 0
    assert record.payload_sha256 is None
    assert record.error is not None


async def test_recording_failure_does_not_break_the_scrape() -> None:
    payload = _payload([_row(1)])

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    def record(_record: RunRecord) -> None:
        raise RuntimeError("runs table missing")

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_events_by_year([2023], client=client, record=record)

    assert results[0].error is None
    assert results[0].received_count == 1

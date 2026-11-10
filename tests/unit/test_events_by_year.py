"""Unit tests for the events-by-year collector. No network, no database."""

import asyncio
import json
import logging
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
    extract_counts,
    extract_rows,
    main,
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


def _requested_year(request: httpx.Request) -> int:
    """Return the year a calendar request asked for, read from its body."""
    body = json.loads(request.content)
    inner_filter = json.loads(body["custom_filter"])
    return int(inner_filter[0]["value"])


def test_extract_rows_rejects_the_wrong_envelope() -> None:
    with pytest.raises(PayloadShapeError):
        extract_rows({"rows": []})


def test_extract_rows_accepts_an_empty_year() -> None:
    """A year with no tournaments is valid: the envelope exists, rows is empty."""
    assert extract_rows(_payload([])) == []


def test_extract_rows_rejects_a_missing_rows_key() -> None:
    with pytest.raises(PayloadShapeError):
        extract_rows([{"Count": 0}])


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


def test_events_start_year_defaults_to_the_first_year_with_data() -> None:
    """1926 is the earliest year the API returns tournaments for (measured)."""
    assert _settings().events_start_year == 1926


def test_chunk_size_is_shared_by_the_collectors() -> None:
    assert _settings().chunk_size == 50


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


@pytest.mark.parametrize("flag", ["--limit", "--chunk-size"])
def test_main_rejects_non_positive_numbers(flag: str) -> None:
    """Argparse handles it: usage on stderr and exit code 2, before any I/O."""
    with pytest.raises(SystemExit) as caught:
        main([flag, "0"])

    assert caught.value.code == 2


async def test_chunk_progress_is_logged_once_per_chunk(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One INFO record per chunk; the per-year records are DEBUG-only."""
    caplog.set_level(logging.INFO, logger="tt_stats.events.collect.events_by_year")
    payload = _payload([_row(1)])

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        await collect_events_by_year([2021, 2022, 2023], client=client, chunk_size=2)

    records = [r for r in caplog.records if r.getMessage() == "events_chunk"]
    extras: list[dict[str, Any]] = [dict(record.__dict__) for record in records]

    assert [extra["chunk"] for extra in extras] == [1, 2]
    assert [extra["years"] for extra in extras] == [2, 1]
    assert sum(extra["events"] for extra in extras) == 3

    messages = {record.getMessage() for record in caplog.records}
    assert "events_year_saved" not in messages


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


async def test_recording_failure_does_not_break_the_collect() -> None:
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


async def test_empty_year_is_a_success_and_still_lands() -> None:
    """A quiet year is recorded once, so the queue stops asking for it."""
    payload = _payload([])
    landed: list[tuple[int, int]] = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    def land(summary: YearPayloadSummary, data: object) -> None:
        landed.append((summary.year, summary.received_count))

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_events_by_year(
            [1934], client=client, land=land, reasons={1934: "backfill"}
        )

    result = results[0]
    assert result.error is None
    assert result.received_count == 0
    assert result.source_final_count == 0
    assert result.reason == "backfill"
    assert landed == [(1934, 0)]


async def test_chunks_bound_concurrency_and_land_in_order() -> None:
    """Five years, chunk size 2: peak parallelism is 2, landing stays ordered."""
    payload = _payload([_row(1)])
    years = [2021, 2022, 2023, 2024, 2025]
    in_flight = 0
    peak = 0
    landed: list[int] = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        # Yield so the sibling request in the same chunk can overlap.
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(200, json=payload)

    def land(summary: YearPayloadSummary, data: object) -> None:
        landed.append(summary.year)

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_events_by_year(
            years, client=client, land=land, chunk_size=2
        )

    assert [summary.year for summary in results] == years
    assert peak == 2
    assert landed == years


async def test_one_failed_year_does_not_stop_its_chunk() -> None:
    """A 5xx for one year leaves the rest of the chunk untouched."""
    payload = _payload([_row(1)])
    landed: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if _requested_year(request) == 2024:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json=payload)

    def land(summary: YearPayloadSummary, data: object) -> None:
        landed.append(summary.year)

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_events_by_year(
            [2023, 2024, 2025], client=client, land=land, chunk_size=3
        )

    by_year = {summary.year: summary for summary in results}
    assert by_year[2024].error is not None
    assert by_year[2024].status_code == 500
    assert by_year[2023].error is None
    assert by_year[2025].error is None
    assert landed == [2023, 2025]


async def test_chunk_size_must_be_positive() -> None:
    payload = _payload([_row(1)])

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(ValueError, match="chunk size"):
            await collect_events_by_year([2023], client=client, chunk_size=0)

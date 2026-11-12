"""Unit tests for the event_sponsors collector. No network, no database."""

import asyncio
import hashlib
import json
import logging
from typing import Any

import httpx
import pytest

from tt_stats.core.clients.wtt_api import WttClient
from tt_stats.core.config import Settings
from tt_stats.core.ingest_runs import RunRecord
from tt_stats.event_sponsors.collect.event_sponsors import (
    EventSponsorsSummary,
    PayloadShapeError,
    collect_event_sponsors,
    extract_rows,
    main,
    validate_rows,
)

EVENT_ID = 3238


def _settings(**overrides: Any) -> Settings:
    """Build isolated settings that never read the real .env file."""
    values: dict[str, Any] = {
        "postgres_password": "test",
        "max_retry_attempts": 1,
        "retry_wait_min_seconds": 0.001,
        "retry_wait_max_seconds": 0.001,
        "request_jitter_seconds": 0.0,
    }
    values.update(overrides)
    return Settings(**values)


def _sponsor(sponsor_id: int, event_id: int = EVENT_ID) -> dict[str, Any]:
    return {
        "eventId": event_id,
        "eventSponsorId": sponsor_id,
        "sponsorTypeId": 15,
        "logo": "org_logos/example.png",
        "darkLogo": None,
        "sponsorLink": "https://example.com/",
        "sponsorTypeName": "Global Premier Partner",
    }


def _requested_event(request: httpx.Request) -> int:
    """Return the event id encoded at the end of the request path."""
    return int(request.url.path.rsplit("/", maxsplit=1)[-1])


def test_extract_rows_accepts_an_empty_list() -> None:
    """``[]`` is what the API sends when an event has no sponsors."""
    assert extract_rows([]) == []


def test_extract_rows_rejects_a_non_list() -> None:
    with pytest.raises(PayloadShapeError, match="expected a list"):
        extract_rows({"rows": []})


def test_extract_rows_rejects_a_list_of_non_objects() -> None:
    with pytest.raises(PayloadShapeError, match="row 0 is not an object"):
        extract_rows(["not a sponsor"])


def test_validate_rows_accepts_a_complete_row() -> None:
    assert validate_rows([_sponsor(5845)], event_id=EVENT_ID) == []


def test_validate_rows_rejects_another_events_rows() -> None:
    problems = validate_rows([_sponsor(5845, event_id=1234)], event_id=EVENT_ID)

    assert problems
    assert "does not match requested event" in problems[0]


def test_validate_rows_reports_missing_keys() -> None:
    row = _sponsor(5845)
    del row["eventSponsorId"]

    problems = validate_rows([row], event_id=EVENT_ID)

    assert problems
    assert "eventSponsorId" in problems[0]


def test_validate_rows_rejects_null_sponsor_id() -> None:
    row = _sponsor(5845)
    row["eventSponsorId"] = None

    problems = validate_rows([row], event_id=EVENT_ID)

    assert problems
    assert "eventSponsorId" in problems[0]


async def test_collect_returns_a_summary() -> None:
    payload = [_sponsor(5845), _sponsor(3251)]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_event_sponsors([EVENT_ID], client=client)

    result = results[0]
    assert result.error is None
    assert result.status_code == 200
    assert result.received_count == 2
    assert result.payload_sha256 is not None
    assert len(result.payload_sha256) == 64


async def test_empty_sponsor_list_is_a_success_and_still_lands() -> None:
    landed: list[tuple[int, int]] = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    def land(summary: EventSponsorsSummary, data: object) -> None:
        landed.append((summary.event_id, summary.received_count))

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_event_sponsors(
            [928], client=client, land=land, reasons={928: "new"}
        )

    result = results[0]
    assert result.error is None
    assert result.received_count == 0
    assert result.reason == "new"
    assert landed == [(928, 0)]


async def test_chunks_bound_concurrency_and_land_in_order() -> None:
    events = [3238, 3239, 3240, 3241, 3242]
    in_flight = 0
    peak = 0
    landed: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(200, json=[_sponsor(1, _requested_event(request))])

    def land(summary: EventSponsorsSummary, data: object) -> None:
        landed.append(summary.event_id)

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_event_sponsors(
            events, client=client, land=land, chunk_size=2
        )

    assert [summary.event_id for summary in results] == events
    assert peak == 2
    assert landed == events


async def test_one_failed_event_does_not_stop_its_chunk() -> None:
    landed: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if _requested_event(request) == 3239:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json=[_sponsor(1, _requested_event(request))])

    def land(summary: EventSponsorsSummary, data: object) -> None:
        landed.append(summary.event_id)

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_event_sponsors(
            [3238, 3239, 3240], client=client, land=land, chunk_size=3
        )

    by_event = {summary.event_id: summary for summary in results}
    assert by_event[3239].error is not None
    assert by_event[3239].status_code == 500
    assert by_event[3238].error is None
    assert by_event[3240].error is None
    assert landed == [3238, 3240]


async def test_landing_failure_marks_the_event_failed() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_sponsor(1)])

    def land(summary: EventSponsorsSummary, data: object) -> None:
        raise RuntimeError("db down")

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_event_sponsors([EVENT_ID], client=client, land=land)

    assert results[0].error == "landing failed: db down"


async def test_collect_records_a_run_per_event() -> None:
    recorded: list[RunRecord] = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_sponsor(5845)])

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        await collect_event_sponsors([EVENT_ID], client=client, record=recorded.append)

    assert len(recorded) == 1
    record = recorded[0]
    assert record.source == "wtt_geteventequipmentwithlogo"
    assert record.entity_key == "3238"
    assert record.status == "succeeded"
    assert record.http_status == 200
    assert record.received_count == 1


async def test_recording_failure_does_not_break_the_collect() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_sponsor(5845)])

    def record(_record: RunRecord) -> None:
        raise RuntimeError("runs table missing")

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_event_sponsors([EVENT_ID], client=client, record=record)

    assert results[0].error is None
    assert results[0].received_count == 1


async def test_chunk_size_must_be_positive() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_sponsor(5845)])

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(ValueError, match="chunk size"):
            await collect_event_sponsors([EVENT_ID], client=client, chunk_size=0)


@pytest.mark.parametrize("flag", ["--limit", "--chunk-size"])
def test_main_rejects_non_positive_numbers(flag: str) -> None:
    """Argparse handles it: usage on stderr and exit code 2, before any I/O."""
    with pytest.raises(SystemExit) as caught:
        main([flag, "0"])

    assert caught.value.code == 2


async def test_chunk_progress_is_logged_once_per_chunk(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Chunk lines carry progress; per-event records are DEBUG-only."""
    caplog.set_level(
        logging.INFO,
        logger="tt_stats.event_sponsors.collect.event_sponsors",
    )
    events = [3238, 3239, 3240, 3241, 3242]

    async def handler(request: httpx.Request) -> httpx.Response:
        event_id = _requested_event(request)
        if event_id == 3240:
            return httpx.Response(500, json={"error": "boom"})
        if event_id in (3241, 3242):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[_sponsor(1, event_id), _sponsor(2, event_id)])

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        await collect_event_sponsors(events, client=client, chunk_size=2)

    records = [r for r in caplog.records if r.getMessage() == "event_sponsors_chunk"]
    extras: list[dict[str, Any]] = [dict(record.__dict__) for record in records]

    assert [extra["chunk"] for extra in extras] == [1, 2, 3]
    assert [extra["events"] for extra in extras] == [2, 2, 1]
    assert sum(extra["sponsors"] for extra in extras) == 4
    assert sum(extra["empty"] for extra in extras) == 2
    assert sum(extra["failed"] for extra in extras) == 1

    messages = {record.getMessage() for record in caplog.records}
    assert "event_sponsors_saved" not in messages
    assert "event_sponsors_empty" not in messages


async def test_payload_is_hashed_from_the_original_bytes() -> None:
    payload = [_sponsor(5845)]
    body = json.dumps(payload).encode("utf-8")

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=body, headers={"content-type": "application/json"}
        )

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_event_sponsors([EVENT_ID], client=client)

    assert results[0].payload_sha256 == hashlib.sha256(body).hexdigest()

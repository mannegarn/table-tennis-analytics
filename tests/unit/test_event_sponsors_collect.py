"""Unit tests for the event-sponsors endpoint. No network, no database.

The shared fetch/land/record loop is tested once, in test_collector.py. These
cover the event-sponsors payload rules and its wiring into that loop.
"""

from typing import Any

import httpx
import pytest

from tt_stats.core.clients.wtt_api import WttClient
from tt_stats.core.collector import FetchSummary, PayloadShapeError, collect
from tt_stats.core.config import Settings
from tt_stats.core.ingest_runs import RunRecord
from tt_stats.event_sponsors.collect import EVENT_SPONSORS, check_payload, validate_rows

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


def test_check_payload_accepts_an_empty_list() -> None:
    """``[]`` is what the API sends when an event has no sponsors."""
    assert check_payload(EVENT_ID, []).received_count == 0


def test_check_payload_rejects_a_non_list() -> None:
    with pytest.raises(PayloadShapeError, match="expected a list"):
        check_payload(EVENT_ID, {"rows": []})


def test_check_payload_rejects_a_list_of_non_objects() -> None:
    with pytest.raises(PayloadShapeError, match="row 0 is not an object"):
        check_payload(EVENT_ID, ["not a sponsor"])


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


async def test_collect_fetches_lands_and_records_an_event() -> None:
    """The endpoint asks for the right event and lands the payload as received."""
    payload = [_sponsor(5845), _sponsor(3251)]
    requested: list[int] = []
    landed: list[tuple[int, Any]] = []
    recorded: list[RunRecord] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested.append(_requested_event(request))
        return httpx.Response(200, json=payload)

    def land(summary: FetchSummary[int], data: Any) -> None:
        landed.append((summary.key, data))

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect(
            EVENT_SPONSORS, [EVENT_ID], client=client, land=land, record=recorded.extend
        )

    result = results[0]
    assert requested == [EVENT_ID]
    assert result.error is None
    assert result.received_count == 2
    assert landed == [(EVENT_ID, payload)]
    assert [(r.source, r.entity_key, r.status) for r in recorded] == [
        ("wtt_geteventequipmentwithlogo", str(EVENT_ID), "succeeded")
    ]


async def test_another_events_sponsor_fail_and_are_not_landed() -> None:
    """A wrong EventId parameter must fail loudly, not reparent the rows."""
    landed: list[int] = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_sponsor(5845, 1234)])

    def land(summary: FetchSummary[int], _data: Any) -> None:
        landed.append(summary.key)

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect(EVENT_SPONSORS, [EVENT_ID], client=client, land=land)

    assert results[0].error is not None
    assert "does not match requested event" in results[0].error
    assert landed == []


async def test_empty_sponsor_list_is_a_success_and_still_lands() -> None:
    """``[]`` is how WTT says an event has no sponsors; landing it stops the asking."""
    landed: list[tuple[int, int]] = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    def land(summary: FetchSummary[int], _data: Any) -> None:
        landed.append((summary.key, summary.received_count))

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect(
            EVENT_SPONSORS, [928], client=client, land=land, reasons={928: "new"}
        )

    result = results[0]
    assert result.error is None
    assert result.received_count == 0
    assert result.reason == "new"
    assert landed == [(928, 0)]

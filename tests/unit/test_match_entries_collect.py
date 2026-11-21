"""Unit tests for the match-list endpoint. No network, no database.

The shared fetch/land/record loop is tested once, in test_collector.py. These
cover the match-list payload rules and its wiring into that loop.
"""

from typing import Any

import httpx
import pytest

from tt_stats.core.clients.wtt_api import WttClient
from tt_stats.core.collector import FetchSummary, PayloadShapeError, collect
from tt_stats.core.config import Settings
from tt_stats.core.ingest_runs import RunRecord
from tt_stats.matches.collect.match_entries import (
    MATCH_ENTRIES,
    check_payload,
    validate_rows,
)

EVENT_ID = 3098


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


def _match(match_id: int, event_id: int = EVENT_ID) -> dict[str, Any]:
    return {
        "id": match_id,
        "eventId": str(event_id),
        "documentCode": "TTEMSINGLES-----------R64-002500----------",
        "subEventType": "Men Singles",
        "fullResults": "OFFICIAL",
        "startDateLocal": "2025-09-29T18:35:00",
        "match_card": None,
        "extended_info": None,
        "messagePayload": None,
    }


def _requested_event(request: httpx.Request) -> int:
    """Return the event id a match-list request asked for."""
    return int(request.url.params["EventId"])


def test_check_payload_accepts_an_empty_list() -> None:
    """``[]`` is what the API sends for an event with no published matches."""
    assert check_payload(EVENT_ID, []).received_count == 0


def test_check_payload_rejects_a_non_list() -> None:
    with pytest.raises(PayloadShapeError, match="expected a list"):
        check_payload(EVENT_ID, {"rows": []})


def test_check_payload_rejects_a_list_of_non_objects() -> None:
    with pytest.raises(PayloadShapeError, match="row 0 is not an object"):
        check_payload(EVENT_ID, ["not a match"])


def test_validate_rows_accepts_a_complete_row() -> None:
    assert validate_rows([_match(1)], event_id=EVENT_ID) == []


def test_validate_rows_rejects_another_events_rows() -> None:
    """A wrong EventId parameter must fail loudly, not reparent the matches."""
    problems = validate_rows([_match(1, event_id=1234)], event_id=EVENT_ID)

    assert problems
    assert "does not match requested event" in problems[0]


def test_validate_rows_reports_missing_keys() -> None:
    row = _match(1)
    del row["documentCode"]

    problems = validate_rows([row], event_id=EVENT_ID)

    assert problems
    assert "documentCode" in problems[0]


@pytest.mark.parametrize("start_date", ["", None])
def test_validate_rows_accepts_a_missing_or_blank_start_date(
    start_date: str | None,
) -> None:
    """424 measured rows send blank or omit startDateLocal altogether."""
    row = _match(1)
    if start_date is None:
        del row["startDateLocal"]
    else:
        row["startDateLocal"] = start_date

    assert validate_rows([row], event_id=EVENT_ID) == []


@pytest.mark.parametrize("bad_code", ["", "   ", None])
def test_validate_rows_rejects_a_blank_or_null_document_code(
    bad_code: str | None,
) -> None:
    row = _match(1)
    row["documentCode"] = bad_code

    problems = validate_rows([row], event_id=EVENT_ID)

    assert problems
    assert "documentCode" in problems[0]


def test_validate_rows_allows_omitted_payload_only_fields() -> None:
    """``id``/``iD``, ``subEventType`` and ``fullResults`` are payload-only now."""
    row = _match(1)
    for key in ("id", "subEventType", "fullResults", "startDateLocal"):
        del row[key]

    assert validate_rows([row], event_id=EVENT_ID) == []


async def test_collect_fetches_lands_and_records_an_event() -> None:
    """The endpoint asks for the right event and lands the payload as received."""
    payload = [_match(1), _match(2)]
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
            MATCH_ENTRIES, [EVENT_ID], client=client, land=land, record=recorded.extend
        )

    result = results[0]
    assert requested == [EVENT_ID]
    assert result.error is None
    assert result.received_count == 2
    assert landed == [(EVENT_ID, payload)]
    assert [(r.endpoint, r.entity_key, r.status) for r in recorded] == [
        ("wtt_getofficialresult", str(EVENT_ID), "succeeded")
    ]


async def test_another_events_match_fail_and_are_not_landed() -> None:
    """A wrong EventId parameter must fail loudly, not reparent the rows."""
    landed: list[int] = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_match(1, 1234)])

    def land(summary: FetchSummary[int], _data: Any) -> None:
        landed.append(summary.key)

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect(MATCH_ENTRIES, [EVENT_ID], client=client, land=land)

    assert results[0].error is not None
    assert "does not match requested event" in results[0].error
    assert landed == []


async def test_empty_match_list_is_a_success_and_still_lands() -> None:
    """``[]`` is how WTT says an event has no matches; landing it stops the asking."""
    landed: list[tuple[int, int]] = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    def land(summary: FetchSummary[int], _data: Any) -> None:
        landed.append((summary.key, summary.received_count))

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect(
            MATCH_ENTRIES, [928], client=client, land=land, reasons={928: "new"}
        )

    result = results[0]
    assert result.error is None
    assert result.received_count == 0
    assert result.reason == "new"
    assert landed == [(928, 0)]

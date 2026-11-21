"""Unit tests for the event-calendar endpoint. No network, no database.

The shared fetch/land/record loop is tested once, in test_collector.py. These
cover the calendar's payload rules and its wiring into that loop.
"""

import json
from typing import Any

import httpx
import pytest

from tt_stats.core.clients.wtt_api import WttClient
from tt_stats.core.collector import FetchSummary, PayloadShapeError, collect
from tt_stats.core.config import Settings
from tt_stats.core.ingest_runs import RunRecord
from tt_stats.events.collect import EVENTS, check_payload, validate_rows


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


def test_check_payload_rejects_the_wrong_envelope() -> None:
    with pytest.raises(PayloadShapeError):
        check_payload(2023, {"rows": []})


def test_check_payload_accepts_an_empty_year() -> None:
    """A year with no tournaments is valid: the envelope exists, rows is empty."""
    checked = check_payload(1934, _payload([]))

    assert checked.error is None
    assert checked.received_count == 0


def test_check_payload_rejects_a_missing_rows_key() -> None:
    with pytest.raises(PayloadShapeError):
        check_payload(2023, [{"Count": 0}])


def test_check_payload_reads_both_source_counts() -> None:
    checked = check_payload(2023, _payload([_row(1)], original=7))

    assert checked.facts == {"source_final_count": 1, "source_original_count": 7}


def test_check_payload_flags_a_count_mismatch() -> None:
    checked = check_payload(2023, _payload([_row(1)], count=2))

    assert checked.received_count == 1
    assert checked.error is not None
    assert "count mismatch" in checked.error


def test_check_payload_requires_a_count() -> None:
    payload = _payload([_row(1)])
    del payload[0]["Count"]

    checked = check_payload(2023, payload)

    assert checked.error is not None
    assert "count mismatch" in checked.error


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


async def test_collect_fetches_lands_and_records_a_year() -> None:
    """The calendar endpoint asks for the right year and lands WTT's counts."""
    payload = _payload([_row(1), _row(2)], original=5)
    requested: list[int] = []
    landed: list[tuple[int, Any]] = []
    recorded: list[RunRecord] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested.append(_requested_year(request))
        return httpx.Response(200, json=payload)

    def land(summary: FetchSummary[int], data: Any) -> None:
        landed.append((summary.key, data))

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect(
            EVENTS, [2023], client=client, land=land, record=recorded.extend
        )

    result = results[0]
    assert requested == [2023]
    assert result.error is None
    assert result.received_count == 2
    assert result.facts == {"source_final_count": 2, "source_original_count": 5}
    assert landed == [(2023, payload)]
    assert [(r.endpoint, r.entity_key, r.status) for r in recorded] == [
        ("wtt_eventcalendar", "2023", "succeeded")
    ]


async def test_empty_year_is_a_success_and_still_lands() -> None:
    """A quiet year is recorded once, so the queue stops asking for it."""
    payload = _payload([])
    landed: list[tuple[int, int]] = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    def land(summary: FetchSummary[int], _data: object) -> None:
        landed.append((summary.key, summary.received_count))

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect(
            EVENTS, [1934], client=client, land=land, reasons={1934: "backfill"}
        )

    result = results[0]
    assert result.error is None
    assert result.received_count == 0
    assert result.facts["source_final_count"] == 0
    assert result.reason == "backfill"
    assert landed == [(1934, 0)]

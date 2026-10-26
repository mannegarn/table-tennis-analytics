"""Unit tests for the events-by-year collector. No network, no database."""

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from tt_stats.core.clients.wtt_api import WttClient
from tt_stats.core.config import Settings
from tt_stats.events.collect.events_by_year import (
    PayloadShapeError,
    collect_events_by_year,
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


async def test_collect_writes_payload_and_meta(tmp_path: Path) -> None:
    payload = _payload([_row(1), _row(2)])

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_events_by_year(
            [2023], client=client, output_dir=tmp_path
        )

    result = results[0]
    assert result.error is None
    assert result.status_code == 200
    assert result.row_count == 2
    assert result.api_count == 2
    assert result.original_count == 2
    assert result.payload_path == tmp_path / "events_2023.json"
    assert result.payload_path is not None
    assert hashlib.sha256(result.payload_path.read_bytes()).hexdigest() == (
        result.payload_sha256
    )

    meta = json.loads((tmp_path / "events_2023.meta.json").read_text())
    assert meta["year"] == 2023
    assert meta["status_code"] == 200
    assert meta["row_count"] == 2


async def test_count_mismatch_is_an_error_but_the_payload_is_kept(
    tmp_path: Path,
) -> None:
    payload = _payload([_row(1)], count=2)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_events_by_year(
            [2023], client=client, output_dir=tmp_path
        )

    result = results[0]
    assert result.error is not None
    assert "count mismatch" in result.error
    assert result.payload_path is not None
    assert result.payload_path.exists()


async def test_http_failure_writes_meta_with_the_status(tmp_path: Path) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "unavailable"})

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_events_by_year(
            [2023], client=client, output_dir=tmp_path
        )

    result = results[0]
    assert result.error is not None
    assert result.status_code == 503
    assert result.payload_path is None

    meta = json.loads((tmp_path / "events_2023.meta.json").read_text())
    assert meta["status_code"] == 503
    assert meta["error"]

"""Unit tests for the shared collector loop. No network, no database.

The per-endpoint test files cover each endpoint's payload checks and wiring.
These cover the parts only the shared loop has: landing styles, 404 handling,
payload replacement and chunk-landing failure.
"""

import json
from collections.abc import Sequence
from typing import Any

import pytest

from tt_stats.core.clients.wtt_api import WttClient, WttRequestError, WttResponse
from tt_stats.core.collector import (
    EMPTY_SHA256,
    Checked,
    Endpoint,
    FetchSummary,
    PayloadShapeError,
    collect,
    object_rows,
    rows_error,
)
from tt_stats.core.config import Settings
from tt_stats.core.ingest_runs import RunRecord

#: Keys the fake endpoint answers with a 404 or a 500 instead of a payload.
NOT_FOUND = 404
SERVER_ERROR = 500


def _client() -> WttClient:
    """A client that never reads the real .env; the fake fetch never uses it."""
    values: dict[str, Any] = {"postgres_password": "test"}
    return WttClient(settings=Settings(**values))


async def _fake_fetch(_client: WttClient, key: int) -> WttResponse:
    if key in (NOT_FOUND, SERVER_ERROR):
        raise WttRequestError(f"HTTP {key}", status_code=key)
    data = [{"n": n} for n in range(key)]
    return WttResponse(status_code=200, content=json.dumps(data).encode(), data=data)


def _endpoint(**overrides: Any) -> Endpoint[int]:
    values: dict[str, Any] = {
        "name": "fake",
        "source": "fake_source",
        "unit": "item",
        "found_label": "rows",
        "fetch": _fake_fetch,
        "check": lambda _key, payload: Checked(len(object_rows(payload, what="rows"))),
        "raw_table": "ingest.fake_raw",
    }
    values.update(overrides)
    return Endpoint[int](**values)


async def test_land_and_land_chunk_are_exclusive() -> None:
    async with _client() as client:
        with pytest.raises(ValueError, match="not both"):
            await collect(
                _endpoint(),
                [1],
                client=client,
                land=lambda _s, _p: None,
                land_chunk=lambda _items: None,
            )


async def test_404_fails_unless_the_endpoint_names_a_payload() -> None:
    async with _client() as client:
        strict = await collect(_endpoint(), [NOT_FOUND], client=client)
        landed: list[Any] = []
        lenient = await collect(
            _endpoint(not_found_payload=[]),
            [NOT_FOUND],
            client=client,
            land=lambda _summary, payload: landed.append(payload),
        )

    assert strict[0].error is not None
    assert strict[0].payload_sha256 is None
    assert lenient[0].error is None
    assert lenient[0].status_code == NOT_FOUND
    assert lenient[0].payload_sha256 == EMPTY_SHA256
    assert landed == [[]]


async def test_a_shape_error_fails_the_item_and_skips_landing() -> None:
    def check(_key: int, _payload: Any) -> Checked:
        raise PayloadShapeError("not the documented shape")

    landed: list[int] = []
    async with _client() as client:
        results = await collect(
            _endpoint(check=check),
            [2],
            client=client,
            land=lambda summary, _payload: landed.append(summary.key),
        )

    assert results[0].error == "payload shape error: not the documented shape"
    assert landed == []


async def test_checked_payload_replaces_the_response_body() -> None:
    landed: list[Any] = []
    async with _client() as client:
        await collect(
            _endpoint(check=lambda _key, _payload: Checked(1, payload="decoded")),
            [2],
            client=client,
            land=lambda _summary, payload: landed.append(payload),
        )

    assert landed == ["decoded"]


async def test_checked_facts_reach_the_summary() -> None:
    async with _client() as client:
        results = await collect(
            _endpoint(check=lambda _key, _payload: Checked(2, facts={"Count": 2})),
            [2],
            client=client,
        )

    assert results[0].facts == {"Count": 2}


async def test_chunk_landing_failure_stops_the_run_and_records_it() -> None:
    """Rolled-back items are recorded as failed, not as succeeded."""
    recorded: list[RunRecord] = []

    def land_chunk(_items: Sequence[tuple[FetchSummary[int], Any]]) -> None:
        raise RuntimeError("db down")

    async with _client() as client:
        with pytest.raises(RuntimeError, match="db down"):
            await collect(
                _endpoint(),
                [1, SERVER_ERROR, 3, 4],
                client=client,
                land_chunk=land_chunk,
                record=recorded.extend,
                chunk_size=3,
            )

    # The first chunk is recorded; the second chunk, key 4, never ran.
    by_key = {run.entity_key: run for run in recorded}
    assert list(by_key) == ["1", str(SERVER_ERROR), "3"]
    assert all(run.status == "failed" for run in recorded)
    assert by_key["1"].error == "chunk landing failed: db down"
    assert by_key["3"].error == "chunk landing failed: db down"
    # An item that failed on its own keeps its own reason.
    assert by_key[str(SERVER_ERROR)].error == f"HTTP {SERVER_ERROR}"


async def test_runs_use_the_endpoint_source_and_entity_key() -> None:
    recorded: list[RunRecord] = []
    async with _client() as client:
        await collect(
            _endpoint(entity_key=lambda key: f"item-{key}"),
            [1, SERVER_ERROR],
            client=client,
            record=recorded.extend,
        )

    assert [(r.source, r.entity_key, r.status) for r in recorded] == [
        ("fake_source", "item-1", "succeeded"),
        ("fake_source", f"item-{SERVER_ERROR}", "failed"),
    ]


def test_rows_error_folds_at_most_five_problems() -> None:
    assert rows_error([]) is None
    error = rows_error([f"p{n}" for n in range(7)])
    assert error == "row validation failed: p0; p1; p2; p3; p4"

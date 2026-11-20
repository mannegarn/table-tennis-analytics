"""Unit tests for the shared collector loop. No network, no database.

Everything here runs against a fake endpoint, because the loop is the same for
every endpoint. The per-endpoint test files cover only what differs: payload
rules, and one end-to-end test of each endpoint's wiring (request, source,
entity key, landing).
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Sequence
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
from tt_stats.event_sponsors.collect import main as event_sponsors_main
from tt_stats.events.collect import main as events_main
from tt_stats.matches.collect.match_commentary import main as match_commentary_main
from tt_stats.matches.collect.match_details import main as match_details_main
from tt_stats.matches.collect.match_entries import main as match_entries_main
from tt_stats.players.collect import main as player_details_main

#: Keys the fake endpoint answers with a 404 or a 500 instead of a payload.
#: Any other key ``n`` returns a list of ``n`` rows, so 0 is an empty payload.
NOT_FOUND = 404
SERVER_ERROR = 500

Landed = list[tuple[int, Any]]


def _client() -> WttClient:
    """A client that never reads the real .env; the fake fetch never uses it."""
    values: dict[str, Any] = {"postgres_password": "test"}
    return WttClient(settings=Settings(**values))


def _body(key: int) -> bytes:
    return json.dumps([{"n": n} for n in range(key)]).encode()


async def _fake_fetch(_client: WttClient, key: int) -> WttResponse:
    if key in (NOT_FOUND, SERVER_ERROR):
        raise WttRequestError(f"HTTP {key}", status_code=key)
    return WttResponse(status_code=200, content=_body(key), data=json.loads(_body(key)))


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


def _land_into(landed: Landed) -> Callable[[FetchSummary[int], Any], None]:
    return lambda summary, payload: landed.append((summary.key, payload))


# --- one item --------------------------------------------------------------


async def test_a_successful_item_is_summarised_landed_and_recorded() -> None:
    landed: Landed = []
    recorded: list[RunRecord] = []
    async with _client() as client:
        results = await collect(
            _endpoint(),
            [2],
            client=client,
            land=_land_into(landed),
            record=recorded.extend,
            reasons={2: "new"},
        )

    summary = results[0]
    assert summary.error is None
    assert summary.status_code == 200
    assert summary.received_count == 2
    assert summary.reason == "new"
    # The hash is of the bytes as received, not of re-serialised JSON.
    assert summary.payload_sha256 == hashlib.sha256(_body(2)).hexdigest()
    assert landed == [(2, [{"n": 0}, {"n": 1}])]

    run = recorded[0]
    assert (run.source, run.entity_key, run.status) == ("fake_source", "2", "succeeded")
    assert (run.http_status, run.received_count, run.error) == (200, 2, None)
    assert run.payload_sha256 == summary.payload_sha256


async def test_an_empty_payload_is_a_success_and_still_lands() -> None:
    """Landing "asked, nothing there" is what stops the queue asking again."""
    landed: Landed = []
    async with _client() as client:
        results = await collect(
            _endpoint(), [0], client=client, land=_land_into(landed)
        )

    assert results[0].error is None
    assert results[0].received_count == 0
    assert landed == [(0, [])]


async def test_a_failed_request_is_summarised_and_recorded_not_landed() -> None:
    landed: Landed = []
    recorded: list[RunRecord] = []
    async with _client() as client:
        results = await collect(
            _endpoint(),
            [SERVER_ERROR],
            client=client,
            land=_land_into(landed),
            record=recorded.extend,
        )

    summary = results[0]
    assert summary.error == f"HTTP {SERVER_ERROR}"
    assert summary.status_code == SERVER_ERROR
    assert summary.payload_sha256 is None
    assert landed == []

    run = recorded[0]
    assert (run.status, run.http_status, run.received_count) == (
        "failed",
        SERVER_ERROR,
        0,
    )
    assert run.payload_sha256 is None
    assert run.error == f"HTTP {SERVER_ERROR}"


async def test_404_fails_unless_the_endpoint_names_a_payload() -> None:
    async with _client() as client:
        strict = await collect(_endpoint(), [NOT_FOUND], client=client)
        landed: Landed = []
        lenient = await collect(
            _endpoint(not_found_payload=[]),
            [NOT_FOUND],
            client=client,
            land=_land_into(landed),
        )

    assert strict[0].error is not None
    assert strict[0].payload_sha256 is None
    assert lenient[0].error is None
    assert lenient[0].status_code == NOT_FOUND
    assert lenient[0].payload_sha256 == EMPTY_SHA256
    assert landed == [(NOT_FOUND, [])]


async def test_a_shape_error_fails_the_item_and_skips_landing() -> None:
    def check(_key: int, _payload: Any) -> Checked:
        raise PayloadShapeError("not the documented shape")

    landed: Landed = []
    async with _client() as client:
        results = await collect(
            _endpoint(check=check), [2], client=client, land=_land_into(landed)
        )

    assert results[0].error == "payload shape error: not the documented shape"
    assert landed == []


async def test_a_check_error_fails_the_item_but_keeps_its_count() -> None:
    def check(_key: int, payload: Any) -> Checked:
        return Checked(len(payload), error="count mismatch")

    landed: Landed = []
    async with _client() as client:
        results = await collect(
            _endpoint(check=check), [2], client=client, land=_land_into(landed)
        )

    assert (results[0].error, results[0].received_count) == ("count mismatch", 2)
    assert landed == []


async def test_checked_payload_replaces_the_response_body() -> None:
    landed: Landed = []
    async with _client() as client:
        await collect(
            _endpoint(check=lambda _key, _payload: Checked(1, payload="decoded")),
            [2],
            client=client,
            land=_land_into(landed),
        )

    assert landed == [(2, "decoded")]


async def test_checked_facts_reach_the_summary() -> None:
    async with _client() as client:
        results = await collect(
            _endpoint(check=lambda _key, _payload: Checked(2, facts={"Count": 2})),
            [2],
            client=client,
        )

    assert results[0].facts == {"Count": 2}


async def test_runs_use_the_endpoint_entity_key() -> None:
    recorded: list[RunRecord] = []
    async with _client() as client:
        await collect(
            _endpoint(entity_key=lambda key: f"item-{key}"),
            [1],
            client=client,
            record=recorded.extend,
        )

    assert [run.entity_key for run in recorded] == ["item-1"]


# --- per-item landing (land) ----------------------------------------------


async def test_a_landing_failure_marks_only_that_item_failed() -> None:
    def land(summary: FetchSummary[int], _payload: Any) -> None:
        if summary.key == 2:
            raise RuntimeError("db down")

    async with _client() as client:
        results = await collect(_endpoint(), [1, 2, 3], client=client, land=land)

    assert [summary.error for summary in results] == [
        None,
        "landing failed: db down",
        None,
    ]


async def test_one_failed_item_does_not_stop_its_chunk() -> None:
    landed: Landed = []
    async with _client() as client:
        results = await collect(
            _endpoint(),
            [1, SERVER_ERROR, 3],
            client=client,
            land=_land_into(landed),
            chunk_size=3,
        )

    assert [summary.error is None for summary in results] == [True, False, True]
    assert [key for key, _ in landed] == [1, 3]


async def test_chunks_bound_concurrency_and_land_in_order() -> None:
    """Five keys, chunk size 2: peak parallelism is 2, landing stays ordered."""
    in_flight = 0
    peak = 0

    async def fetch(client: WttClient, key: int) -> WttResponse:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        # Yield so the sibling request in the same chunk can overlap.
        await asyncio.sleep(0.01)
        in_flight -= 1
        return await _fake_fetch(client, key)

    keys = [5, 4, 3, 2, 1]
    landed: Landed = []
    async with _client() as client:
        results = await collect(
            _endpoint(fetch=fetch),
            keys,
            client=client,
            land=_land_into(landed),
            chunk_size=2,
        )

    assert peak == 2
    assert [summary.key for summary in results] == keys
    assert [key for key, _ in landed] == keys


# --- per-chunk landing (land_chunk) ---------------------------------------


async def test_land_chunk_lands_and_records_at_the_end_of_each_chunk() -> None:
    landed_chunks: list[list[int]] = []
    recorded_chunks: list[list[str]] = []

    def land_chunk(items: Sequence[tuple[FetchSummary[int], Any]]) -> None:
        landed_chunks.append([summary.key for summary, _ in items])

    def record(runs: Sequence[RunRecord]) -> None:
        recorded_chunks.append([run.entity_key for run in runs])

    async with _client() as client:
        await collect(
            _endpoint(),
            [1, SERVER_ERROR, 3],
            client=client,
            land_chunk=land_chunk,
            record=record,
            chunk_size=2,
        )

    # The failed item is recorded but never handed to land_chunk.
    assert landed_chunks == [[1], [3]]
    assert recorded_chunks == [["1", str(SERVER_ERROR)], ["3"]]


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


# --- run history, progress, logging ---------------------------------------


async def test_a_recording_failure_does_not_break_the_collect() -> None:
    def record(_runs: Sequence[RunRecord]) -> None:
        raise RuntimeError("runs table missing")

    async with _client() as client:
        results = await collect(_endpoint(), [1], client=client, record=record)

    assert results[0].error is None
    assert results[0].received_count == 1


async def test_progress_is_reported_for_every_item_in_order() -> None:
    reported: list[FetchSummary[int]] = []
    async with _client() as client:
        await collect(
            _endpoint(),
            [1, SERVER_ERROR, 3],
            client=client,
            progress=reported.append,
            chunk_size=2,
        )

    assert [summary.key for summary in reported] == [1, SERVER_ERROR, 3]
    assert [summary.error is None for summary in reported] == [True, False, True]


async def test_chunk_progress_is_logged_once_per_chunk(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One INFO record per chunk; per-item records are DEBUG, failures ERROR."""
    caplog.set_level(logging.INFO, logger="tt_stats.core.collector")

    async with _client() as client:
        await collect(
            _endpoint(), [2, 2, SERVER_ERROR, 0, 0], client=client, chunk_size=2
        )

    records = [r for r in caplog.records if r.getMessage() == "fake_chunk"]
    # Extras are attached to the record's __dict__, not to typed attributes.
    extras: list[dict[str, Any]] = [dict(record.__dict__) for record in records]

    assert [extra["chunk"] for extra in extras] == [1, 2, 3]
    assert [extra["items"] for extra in extras] == [2, 2, 1]
    assert sum(extra["rows"] for extra in extras) == 4
    assert sum(extra["empty"] for extra in extras) == 2
    assert sum(extra["failed"] for extra in extras) == 1

    messages = [record.getMessage() for record in caplog.records]
    assert "fake_saved" not in messages
    assert "fake_empty" not in messages
    assert messages.count("fake_incomplete") == 1


async def test_chunk_size_must_be_positive() -> None:
    async with _client() as client:
        with pytest.raises(ValueError, match="chunk size"):
            await collect(_endpoint(), [1], client=client, chunk_size=0)


def test_rows_error_folds_at_most_five_problems() -> None:
    assert rows_error([]) is None
    error = rows_error([f"p{n}" for n in range(7)])
    assert error == "row validation failed: p0; p1; p2; p3; p4"


# --- console entry points -------------------------------------------------


@pytest.mark.parametrize(
    "main",
    [
        events_main,
        event_sponsors_main,
        match_entries_main,
        match_details_main,
        match_commentary_main,
        player_details_main,
    ],
    ids=[
        "events",
        "event_sponsors",
        "match_entries",
        "match_details",
        "match_commentary",
        "player_details",
    ],
)
@pytest.mark.parametrize("flag", ["--limit", "--chunk-size"])
def test_every_collector_rejects_non_positive_numbers(
    main: Callable[[Sequence[str]], int], flag: str
) -> None:
    """Argparse handles it: usage on stderr and exit code 2, before any I/O."""
    with pytest.raises(SystemExit) as caught:
        main([flag, "0"])

    assert caught.value.code == 2

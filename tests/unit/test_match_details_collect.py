"""Unit tests for the match_details collector. No network, no database."""

import asyncio
import hashlib
import json
from collections.abc import Sequence
from typing import Any

import httpx
import pytest

from tt_stats.core.clients.wtt_api import WttClient
from tt_stats.core.config import Settings
from tt_stats.core.ingest_runs import RunRecord
from tt_stats.matches.collect.match_details import (
    MatchDetailsSummary,
    MatchKey,
    PayloadShapeError,
    collect_match_details,
    main,
    validate_payload,
)

EVENT_ID = 3257
DOCUMENT_CODE = "TTEWSINGLES-----------RND1002700----------"


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


def _payload(
    event_id: int = EVENT_ID,
    document_code: str = DOCUMENT_CODE,
    player_ids: Sequence[str] = ("117455", "146301"),
) -> dict[str, Any]:
    return {
        "eventId": str(event_id),
        "documentCode": document_code,
        "competitiors": [{"players": [{"playerId": pid}]} for pid in player_ids],
    }


def _requested_key(request: httpx.Request) -> MatchKey:
    parts = request.url.path.removesuffix(".json").rsplit("/", maxsplit=2)
    return int(parts[-2]), parts[-1]


def test_validate_payload_counts_distinct_players() -> None:
    count, error = validate_payload(
        _payload(player_ids=("117455", "146301", "117455")),
        event_id=EVENT_ID,
        document_code=DOCUMENT_CODE,
    )
    assert error is None
    assert count == 2


def test_validate_payload_rejects_non_dict() -> None:
    with pytest.raises(PayloadShapeError, match="expected a match object"):
        validate_payload([], event_id=EVENT_ID, document_code=DOCUMENT_CODE)


def test_validate_payload_rejects_mismatched_event_or_document() -> None:
    _, err_event = validate_payload(
        _payload(event_id=9999), event_id=EVENT_ID, document_code=DOCUMENT_CODE
    )
    assert err_event is not None
    assert "eventId" in err_event

    _, err_doc = validate_payload(
        _payload(document_code="OTHER"), event_id=EVENT_ID, document_code=DOCUMENT_CODE
    )
    assert err_doc is not None
    assert "documentCode" in err_doc


async def test_collect_lands_and_records_at_end_of_each_chunk() -> None:
    keys: list[MatchKey] = [
        (EVENT_ID, "DOC1"),
        (EVENT_ID, "DOC2"),
        (EVENT_ID, "DOC3"),
    ]
    landed_chunks: list[list[MatchKey]] = []
    recorded_chunks: list[list[str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        eid, code = _requested_key(request)
        await asyncio.sleep(0.01)
        return httpx.Response(200, json=_payload(eid, code))

    def land_chunk(items: Sequence[tuple[MatchDetailsSummary, Any]]) -> None:
        landed_chunks.append([(s.event_id, s.document_code) for s, _ in items])

    def record_many(runs: Sequence[RunRecord]) -> None:
        recorded_chunks.append([r.entity_key for r in runs])

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_match_details(
            keys,
            client=client,
            land_chunk=land_chunk,
            record_many=record_many,
            chunk_size=2,
        )

    assert len(results) == 3
    assert landed_chunks == [
        [(EVENT_ID, "DOC1"), (EVENT_ID, "DOC2")],
        [(EVENT_ID, "DOC3")],
    ]
    assert recorded_chunks == [
        [f"{EVENT_ID}:DOC1", f"{EVENT_ID}:DOC2"],
        [f"{EVENT_ID}:DOC3"],
    ]


async def test_failed_match_is_recorded_but_excluded_from_chunk_landing() -> None:
    keys: list[MatchKey] = [(EVENT_ID, "OK"), (EVENT_ID, "FAIL")]
    landed_chunks: list[list[MatchKey]] = []
    recorded_runs: list[RunRecord] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        eid, code = _requested_key(request)
        if code == "FAIL":
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json=_payload(eid, code))

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_match_details(
            keys,
            client=client,
            land_chunk=lambda items: landed_chunks.append(
                [(s.event_id, s.document_code) for s, _ in items]
            ),
            record_many=recorded_runs.extend,
            chunk_size=2,
        )

    assert [r.error is None for r in results] == [True, False]
    assert landed_chunks == [[(EVENT_ID, "OK")]]
    assert [r.status for r in recorded_runs] == ["succeeded", "failed"]


async def test_payload_is_hashed_from_the_original_bytes() -> None:
    payload = _payload()
    body = json.dumps(payload).encode("utf-8")

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=body, headers={"content-type": "application/json"}
        )

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_match_details(
            [(EVENT_ID, DOCUMENT_CODE)], client=client
        )

    assert results[0].payload_sha256 == hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize("flag", ["--limit", "--chunk-size"])
def test_main_rejects_non_positive_numbers(flag: str) -> None:
    with pytest.raises(SystemExit) as caught:
        main([flag, "0"])

    assert caught.value.code == 2

"""Unit tests for the player_details collector. No network, no database."""

from collections.abc import Sequence
from typing import Any

import httpx
import pytest

from tt_stats.core.clients.wtt_api import WttClient
from tt_stats.core.config import Settings
from tt_stats.core.ingest_runs import RunRecord
from tt_stats.players.collect.player_details import (
    PayloadShapeError,
    PlayerDetailsSummary,
    collect_player_details,
    main,
    validate_payload,
)

PLAYER_ID = 121558


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


def _payload(player_id: int = PLAYER_ID) -> dict[str, Any]:
    return {
        "Version": "0.1.0",
        "Source": "ITTF/WTT",
        "StatusCode": 200,
        "Result": [
            {
                "IttfId": str(player_id),
                "PlayerName": "WANG Chuqin",
                "CountryCode": "CHN",
                "Gender": "M",
                "Age": "26",
                "Handedness": "Left Hand",
                "Grip": "Shakehand",
            }
        ],
    }


def _requested_player_id(request: httpx.Request) -> int:
    return int(request.url.params["IttfId"])


def test_validate_payload_accepts_valid_and_empty_envelopes() -> None:
    assert validate_payload(_payload(PLAYER_ID), player_id=PLAYER_ID) == (1, None)
    assert validate_payload({"Result": []}, player_id=PLAYER_ID) == (0, None)
    assert validate_payload({"Result": None}, player_id=PLAYER_ID) == (0, None)


def test_validate_payload_detects_mismatched_ittf_id() -> None:
    count, error = validate_payload(_payload(999999), player_id=PLAYER_ID)
    assert count == 0
    assert error is not None
    assert "does not match requested player" in error


def test_validate_payload_rejects_invalid_shapes() -> None:
    with pytest.raises(PayloadShapeError):
        validate_payload(["not", "a", "dict"], player_id=PLAYER_ID)
    with pytest.raises(PayloadShapeError):
        validate_payload({"Result": "bad"}, player_id=PLAYER_ID)
    with pytest.raises(PayloadShapeError):
        validate_payload({"Result": ["not-a-dict"]}, player_id=PLAYER_ID)


async def test_collect_lands_chunks_and_treats_404_as_empty() -> None:
    players = [PLAYER_ID, 404000, 500000]
    landed_chunks: list[list[tuple[int, int, int | None]]] = []
    recorded_runs: list[RunRecord] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        pid = _requested_player_id(request)
        if pid == 404000:
            return httpx.Response(404, json={"error": "not found"})
        if pid == 500000:
            return httpx.Response(500, json={"error": "server error"})
        return httpx.Response(200, json=_payload(pid))

    def land_chunk(items: Sequence[tuple[PlayerDetailsSummary, Any]]) -> None:
        landed_chunks.append(
            [(s.player_id, s.received_count, s.status_code) for s, _ in items]
        )

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_player_details(
            players,
            client=client,
            land_chunk=land_chunk,
            record_many=recorded_runs.extend,
            chunk_size=2,
        )

    assert [r.error is None for r in results] == [True, True, False]
    assert [r.status_code for r in results] == [200, 404, 500]
    assert landed_chunks == [
        [(PLAYER_ID, 1, 200), (404000, 0, 404)],
    ]
    assert [r.status for r in recorded_runs] == ["succeeded", "succeeded", "failed"]


@pytest.mark.parametrize("flag", ["--limit", "--chunk-size"])
def test_main_rejects_non_positive_numbers(flag: str) -> None:
    with pytest.raises(SystemExit) as caught:
        main([flag, "0"])

    assert caught.value.code == 2

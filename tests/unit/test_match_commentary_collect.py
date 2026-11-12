"""Unit tests for the match_commentary collector. No network, no database."""

import asyncio
import json
from collections.abc import Sequence
from typing import Any

import httpx
import pytest

from tt_stats.core.clients.wtt_api import WttClient
from tt_stats.core.config import Settings
from tt_stats.core.ingest_runs import RunRecord
from tt_stats.matches.collect.match_commentary import (
    MatchCommentarySummary,
    MatchKey,
    PayloadShapeError,
    collect_match_commentary,
    decode_commentary_payload,
    main,
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


def _line(seq: int) -> dict[str, Any]:
    return {
        "id": f"id-{seq}",
        "event_doc_code": f"{EVENT_ID}_{DOCUMENT_CODE}",
        "data": {
            "ACTIONTYPE": "PT",
            "SEQUENCENUMBER": str(seq),
            "COMMENTARYTEXT": f"Point {seq}",
        },
        "_ts": 1794324411,
    }


def _requested_key(request: httpx.Request) -> MatchKey:
    parts = request.url.path.rsplit("/", maxsplit=2)
    return int(parts[-2]), parts[-1]


def test_decode_commentary_payload_unwraps_double_encoded_string() -> None:
    raw_str = json.dumps([_line(1), _line(2)])
    rows = decode_commentary_payload(raw_str)
    assert len(rows) == 2
    assert rows[0]["data"]["SEQUENCENUMBER"] == "1"


def test_decode_commentary_payload_accepts_empty_cases() -> None:
    assert decode_commentary_payload([]) == []
    assert decode_commentary_payload("[]") == []
    assert decode_commentary_payload("") == []
    assert decode_commentary_payload(None) == []


def test_decode_commentary_payload_rejects_invalid_shapes() -> None:
    with pytest.raises(PayloadShapeError):
        decode_commentary_payload("not json")
    with pytest.raises(PayloadShapeError):
        decode_commentary_payload({"not": "a list"})


async def test_collect_unwraps_and_lands_by_chunk() -> None:
    keys: list[MatchKey] = [(EVENT_ID, "DOC1"), (EVENT_ID, "DOC2"), (EVENT_ID, "DOC3")]
    landed_chunks: list[list[tuple[MatchKey, int]]] = []
    recorded_chunks: list[list[str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.01)
        return httpx.Response(200, json=json.dumps([_line(1), _line(2)]))

    def land_chunk(items: Sequence[tuple[MatchCommentarySummary, Any]]) -> None:
        landed_chunks.append(
            [((s.event_id, s.document_code), len(rows)) for s, rows in items]
        )

    def record_many(runs: Sequence[RunRecord]) -> None:
        recorded_chunks.append([r.entity_key for r in runs])

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        results = await collect_match_commentary(
            keys,
            client=client,
            land_chunk=land_chunk,
            record_many=record_many,
            chunk_size=2,
        )

    assert [r.received_count for r in results] == [2, 2, 2]
    assert landed_chunks == [
        [((EVENT_ID, "DOC1"), 2), ((EVENT_ID, "DOC2"), 2)],
        [((EVENT_ID, "DOC3"), 2)],
    ]
    assert recorded_chunks == [
        [f"{EVENT_ID}:DOC1", f"{EVENT_ID}:DOC2"],
        [f"{EVENT_ID}:DOC3"],
    ]


@pytest.mark.parametrize("flag", ["--limit", "--chunk-size"])
def test_main_rejects_non_positive_numbers(flag: str) -> None:
    with pytest.raises(SystemExit) as caught:
        main([flag, "0"])

    assert caught.value.code == 2

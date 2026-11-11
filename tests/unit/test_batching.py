"""Unit tests for the shared chunking and fetch-then-settle helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from tt_stats.core.batching import (
    FetchedItem,
    chunks,
    collect_in_chunks,
    reason_counts,
)
from tt_stats.core.clients.wtt_api import WttPayloadError, WttRequestError, WttResponse

if TYPE_CHECKING:
    from collections.abc import Sequence


def test_chunks_splits_items_preserving_order() -> None:
    assert chunks([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert chunks([], 2) == []


def test_chunks_rejects_non_positive_size() -> None:
    with pytest.raises(ValueError, match="chunk size must be at least 1"):
        chunks([1], 0)


def test_reason_counts_tallies_reasons() -> None:
    queue = [(2026, "current"), (2027, "next"), (2025, "backfill"), (2024, "backfill")]
    assert reason_counts(queue) == {"current": 1, "next": 1, "backfill": 2}


async def test_collect_in_chunks_isolates_request_payload_and_unexpected_errors() -> (
    None
):
    """Any failure inside ``fetch`` becomes a failed ``FetchedItem``, not a crash."""
    seen_chunks: list[tuple[int, int, list[FetchedItem[int]]]] = []

    async def fetch(key: int) -> WttResponse:
        if key == 1:
            return WttResponse(status_code=200, content=b"[]", data=[])
        if key == 2:
            raise WttRequestError("upstream 503", status_code=503)
        if key == 3:
            raise WttPayloadError("invalid json")
        raise RuntimeError("unexpected bug")

    def on_chunk(
        index: int,
        total: int,
        settled: Sequence[FetchedItem[int]],
        started_at: datetime,
    ) -> None:
        assert started_at.tzinfo == UTC
        seen_chunks.append((index, total, list(settled)))

    results = await collect_in_chunks(
        [1, 2, 3, 4],
        fetch=fetch,
        settle=lambda item: item,
        on_chunk=on_chunk,
        chunk_size=2,
        reasons={1: "live", 4: "stale"},
    )

    assert [item.key for item in results] == [1, 2, 3, 4]
    assert results[0].response is not None
    assert results[0].error is None
    assert results[0].reason == "live"

    assert results[1].response is None
    assert results[1].status_code == 503
    assert results[1].error == "upstream 503"

    assert results[2].response is None
    assert results[2].status_code is None
    assert results[2].error == "invalid json"

    assert results[3].response is None
    assert results[3].status_code is None
    assert results[3].error == "RuntimeError: unexpected bug"
    assert results[3].reason == "stale"

    assert [(index, total, len(batch)) for index, total, batch in seen_chunks] == [
        (1, 2, 2),
        (2, 2, 2),
    ]

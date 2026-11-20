"""Chunked batching and concurrent fetch-then-settle helpers.

The lowest layer of the collector loop in ``tt_stats.core.collector``: it
knows nothing about payloads, only how to fetch a chunk of keys concurrently
and hand each result, in order, to a ``settle`` callback.

Batching is a **scheduling and commit-granularity** decision, not a concurrency
limit. Concurrency is bounded by the client's semaphore; a batch bounds how much
unlanded work one crash can lose and sets the progress cadence.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from tt_stats.core.clients.wtt_api import WttPayloadError, WttRequestError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from tt_stats.core.clients.wtt_api import WttResponse


@dataclass(frozen=True, slots=True)
class FetchedItem[KeyT]:
    """One settled network attempt, before domain validation and landing."""

    key: KeyT
    started_at: datetime
    fetched_at: datetime
    response: WttResponse | None
    error: str | None
    status_code: int | None
    reason: str | None


def chunks[T](items: Sequence[T], size: int) -> list[list[T]]:
    """Split ``items`` into consecutive batches of at most ``size``.

    Args:
        items: Work to batch, already in priority order.
        size: Maximum batch size. Must be at least 1.

    Returns:
        A list of batches, in the original order. The last batch may be shorter.

    Raises:
        ValueError: If ``size`` is not a positive integer.
    """
    if size < 1:
        raise ValueError(f"chunk size must be at least 1, got {size}")
    return [list(items[index : index + size]) for index in range(0, len(items), size)]


async def _fetch_one[KeyT](
    key: KeyT,
    *,
    fetch: Callable[[KeyT], Awaitable[WttResponse]],
    reason: str | None,
) -> FetchedItem[KeyT]:
    """Run one async fetch and convert WTT errors into a failed item."""
    started_at = datetime.now(tz=UTC)

    try:
        response = await fetch(key)
    except WttRequestError as error:
        return FetchedItem(
            key=key,
            started_at=started_at,
            fetched_at=datetime.now(tz=UTC),
            response=None,
            error=str(error),
            status_code=error.status_code,
            reason=reason,
        )
    except WttPayloadError as error:
        return FetchedItem(
            key=key,
            started_at=started_at,
            fetched_at=datetime.now(tz=UTC),
            response=None,
            error=str(error),
            status_code=None,
            reason=reason,
        )

    return FetchedItem(
        key=key,
        started_at=started_at,
        fetched_at=datetime.now(tz=UTC),
        response=response,
        error=None,
        status_code=response.status_code,
        reason=reason,
    )


async def collect_in_chunks[KeyT, SummaryT](
    items: Sequence[KeyT],
    *,
    fetch: Callable[[KeyT], Awaitable[WttResponse]],
    settle: Callable[[FetchedItem[KeyT]], SummaryT],
    on_chunk: Callable[[int, int, list[SummaryT], datetime], None] | None = None,
    progress: Callable[[SummaryT], None] | None = None,
    chunk_size: int | None = None,
    reasons: Mapping[KeyT, str] | None = None,
) -> list[SummaryT]:
    """Fetch each chunk concurrently, then settle its items sequentially in order.

    One failed fetch or sibling exception never cancels the chunk; it becomes a
    failed ``FetchedItem`` passed to ``settle``. ``chunk_size`` of ``None`` runs
    all items in a single batch.
    """
    summaries: list[SummaryT] = []
    size = chunk_size if chunk_size is not None else max(len(items), 1)
    reason_for = reasons or {}
    batches = chunks(list(items), size)

    for index, chunk in enumerate(batches, start=1):
        chunk_started = datetime.now(tz=UTC)
        outcomes = await asyncio.gather(
            *(
                _fetch_one(key, fetch=fetch, reason=reason_for.get(key))
                for key in chunk
            ),
            # One broken coroutine must not cancel its siblings; it becomes a
            # failed item with the exception as the reason.
            return_exceptions=True,
        )

        settled: list[SummaryT] = []
        for key, outcome in zip(chunk, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                fetched = FetchedItem(
                    key=key,
                    started_at=chunk_started,
                    fetched_at=datetime.now(tz=UTC),
                    response=None,
                    error=f"{type(outcome).__name__}: {outcome}",
                    status_code=None,
                    reason=reason_for.get(key),
                )
            else:
                fetched = outcome

            summary = settle(fetched)
            settled.append(summary)
            summaries.append(summary)
            if progress is not None:
                progress(summary)

        if on_chunk is not None:
            on_chunk(index, len(batches), settled, chunk_started)

    return summaries

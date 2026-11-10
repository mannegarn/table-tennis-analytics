"""Split work into fixed-size batches.

Both collectors batch: the calendar by year, the match list by event. This is
the one implementation, so the batching rule cannot drift between them.

Batching is a **scheduling and commit-granularity** decision, not a concurrency
limit. Concurrency is bounded by the client's semaphore; a batch bounds how much
unlanded work one crash can lose and sets the progress cadence.
"""

from collections.abc import Sequence


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

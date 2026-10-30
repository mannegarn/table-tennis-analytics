"""Terminal progress bars for the collectors.

``tqdm`` owns the terminal while a bar is live: it rewrites a single line in
place. Routine log records never fight it for that line because the console
handler is set to ``WARNING`` (see :mod:`tt_stats.core.logging_config`), while
every ``INFO`` record still lands in ``logs/tt_stats.log``.

Two shapes cover every collector::

    # item by item
    for year in track(years, description="Scraping calendar", unit="year"):
        ...

    # chunked: advance once per committed batch
    with progress_bar(len(events), description="Matches", unit="match") as bar:
        for chunk in chunks:
            bar.update(len(chunk))

Both helpers disable themselves when the stream is not a terminal, so piped,
captured and JSON runs produce no bar at all.
"""

import sys
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any, TextIO

from tqdm import tqdm as _tqdm

# tqdm is generic in the type stubs but is not subscriptable at runtime, so only
# subscript it for the type checker.
if TYPE_CHECKING:
    _Tqdm = _tqdm[Any]
else:
    _Tqdm = _tqdm

_BAR_FORMAT = "{desc}: {n_fmt}/{total_fmt} [{bar:10}] {postfix}"


def progress_bar(
    total: int,
    *,
    description: str,
    unit: str = "item",
    file: TextIO | None = None,
    disable: bool = False,
) -> _Tqdm:
    """Return a bar the caller advances manually.

    Use with ``with`` so the final frame is always written. ``bar.update(n)``
    advances by a batch; ``bar.set_postfix_str(...)`` shows what just finished.
    """
    stream = _stream(file)
    return _tqdm(
        total=total,
        desc=description,
        unit=unit,
        file=stream,
        bar_format=_BAR_FORMAT,
        leave=True,
        disable=disable or not stream.isatty(),
    )


def track[T](
    items: Iterable[T],
    *,
    description: str,
    unit: str = "item",
    file: TextIO | None = None,
    disable: bool = False,
) -> Iterator[T]:
    """Iterate ``items`` under a bar that advances once per item."""
    stream = _stream(file)
    with _tqdm(
        items,
        desc=description,
        unit=unit,
        file=stream,
        bar_format=_BAR_FORMAT,
        leave=True,
        disable=disable or not stream.isatty(),
    ) as bar:
        yield from bar


def _stream(file: TextIO | None) -> TextIO:
    return sys.stdout if file is None else file

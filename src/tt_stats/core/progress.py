"""Terminal progress bars for the collectors.

``tqdm`` owns the terminal while a bar is live: it rewrites a single line in
place. Routine log records never fight it for that line because the console
handler is set to ``WARNING`` (see :mod:`tt_stats.core.logging_config`), while
every ``INFO`` record still lands in ``logs/tt_stats.log``.

Two shapes cover every collector::

    # item by item
    for year in track(years, description="Collecting calendar", unit="year"):
        ...

    # chunked: advance once per committed batch
    with progress_bar(len(events), description="Matches", unit="match") as bar:
        for chunk in chunks:
            bar.update(len(chunk))

Both helpers disable themselves when the stream is not a terminal, so piped,
captured and JSON runs produce no bar at all.

The bar also carries the rate and the elapsed/remaining time, and
:class:`RunTiming` turns a finished run into the one-line summary the collectors
print at the end: ``2838 events in 7m 12s (6.6/s)``.
"""

import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, TextIO

from tqdm import tqdm as _tqdm

# tqdm is generic in the type stubs but is not subscriptable at runtime, so only
# subscript it for the type checker.
if TYPE_CHECKING:
    _Tqdm = _tqdm[Any]
else:
    _Tqdm = _tqdm

#: Elapsed and remaining are tqdm placeholders, so the bar answers "how long
#: left" and "how fast" without the caller computing anything. ``rate_fmt``
#: already appends the unit per second, e.g. ``6.63year/s``.
_BAR_FORMAT = (
    "{desc}: {n_fmt}/{total_fmt} [{bar:10}] "
    "[{elapsed}<{remaining}, {rate_fmt}] {postfix}"
)


def plural(count: int, unit: str) -> str:
    """Return the plural form of ``unit`` unless ``count`` is exactly one.

    Shared so summaries cannot print ``1 events`` or ``2 matchs``.
    """
    if count == 1:
        return unit
    if unit.endswith(("s", "x", "z", "ch", "sh")):
        return f"{unit}es"
    return f"{unit}s"


def format_duration(value: timedelta) -> str:
    """Return a compact duration: ``42s``, ``7m 12s``, ``1h 03m``.

    Hours drop the seconds on purpose: past an hour, the second hand is noise.
    Under ten seconds one decimal is kept, because a fast run reported as ``0s``
    tells you nothing about its throughput. A negative duration (clock
    adjustment, mocked time) clamps to zero rather than printing ``-1s``.
    """
    raw = max(value.total_seconds(), 0.0)
    if raw < 10:
        return f"{raw:.1f}s"

    seconds = int(raw)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


@dataclass(frozen=True, slots=True)
class RunTiming:
    """Wall-clock span of one collector run, with throughput.

    Built from two timestamps the collector takes itself, so it measures the
    whole run including the parts that are not waiting on the network: landing,
    recording and progress drawing.
    """

    started_at: datetime
    finished_at: datetime

    @property
    def elapsed(self) -> timedelta:
        """How long the run took."""
        return self.finished_at - self.started_at

    def rate_per_second(self, count: int) -> float:
        """Units processed per second, or 0.0 for an instantaneous run."""
        seconds = self.elapsed.total_seconds()
        return count / seconds if seconds > 0 else 0.0

    def describe(self, count: int, *, unit: str) -> str:
        """Return ``2,838 events in 7m 12s (6.6/s)``.

        Counts are thousands-separated because these runs are read by a human
        looking at a terminal, not parsed by a program.

        Args:
            count: Units processed, usually the number of years or events.
            unit: Singular noun for one unit.
        """
        return (
            f"{count:,} {plural(count, unit)} in {format_duration(self.elapsed)} "
            f"({self.rate_per_second(count):.1f}/s)"
        )


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

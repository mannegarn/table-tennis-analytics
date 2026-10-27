"""Live progress display for the collectors.

A terminal gets one compact ``tqdm`` bar that updates in place. Piped output
and JSON logs get no bar: JSON already has one record per event, and a bar
cannot render into a file.
"""

import sys
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol, TextIO

from tqdm import tqdm as _tqdm

# tqdm is generic in the type stubs but is not subscriptable at runtime,
# so only subscript it for the type checker.
if TYPE_CHECKING:
    _TqdmBase = _tqdm[Any]
else:
    _TqdmBase = _tqdm


class ProgressReporter(Protocol):
    """Minimal interface for a progress display."""

    @property
    def logs_records(self) -> bool:
        """Whether the collector should also emit per-item log records."""
        ...

    @property
    def prints_summary(self) -> bool:
        """Whether the reporter prints its own start and finish blocks."""
        ...

    def start(self, title: str, context: Mapping[str, str]) -> None:
        """Print the opening block."""
        ...

    def advance(self, message: str) -> None:
        """Mark one item complete and show what it was."""
        ...

    def finish(self, title: str, context: Mapping[str, str]) -> None:
        """Print the closing block."""
        ...

    def close(self) -> None:
        """Finish the display."""
        ...


class NullProgress:
    """Used when a bar cannot render, such as JSON or piped output."""

    logs_records = True
    prints_summary = False

    def start(self, title: str, context: Mapping[str, str]) -> None:
        pass

    def advance(self, message: str) -> None:
        pass

    def finish(self, title: str, context: Mapping[str, str]) -> None:
        pass

    def close(self) -> None:
        pass


class _CompactTqdm(_TqdmBase):
    """tqdm prepends a comma to the postfix; remove it."""

    @staticmethod
    def format_meter(
        n: float,
        total: float,
        elapsed: float,
        *args: Any,
        **kwargs: Any,
    ) -> str:
        # Forward everything: tqdm calls this with **format_dict, which
        # carries keys beyond the documented parameters (file, desc, ...).
        line = _tqdm.format_meter(n, total, elapsed, *args, **kwargs)
        return line.replace("] , ", "] ")


class TqdmProgress:
    """Compact terminal bar that overwrites its own line."""

    logs_records = False
    prints_summary = True

    def __init__(
        self,
        total: int,
        description: str = "Events calendar",
        *,
        file: TextIO | None = None,
        out: TextIO | None = None,
    ) -> None:
        self._total = total
        self._description = description
        self._file = file or sys.stdout
        self._out = out or sys.stdout
        self._bar: _CompactTqdm | None = None

    def start(self, title: str, context: Mapping[str, str]) -> None:
        _write_block(self._out, title, context)
        self._out.write("\n")
        self._out.flush()

        # Create the bar only now, so its first frame cannot appear before
        # the start block and leave a stray 0/N line.
        self._bar = _CompactTqdm(
            total=self._total,
            desc=self._description,
            unit="item",
            file=self._file,
            leave=True,
            bar_format="{desc}: {n_fmt}/{total_fmt} [{bar:10}] {postfix}",
        )

    def advance(self, message: str) -> None:
        if self._bar is None:
            return
        # update() honours mininterval and would drop frames on fast runs, so
        # refresh=True forces one rendered frame per item with its message.
        self._bar.set_postfix_str(message, refresh=False)
        self._bar.update(1)
        self._bar.refresh()

    def finish(self, title: str, context: Mapping[str, str]) -> None:
        # Close the bar before printing, so its final frame cannot land on top
        # of the summary block.
        self.close()
        self._out.write("\n")
        _write_block(self._out, title, context)
        self._out.flush()

    def close(self) -> None:
        if self._bar is not None:
            self._bar.close()
            self._bar = None


def _write_block(file: TextIO, title: str, context: Mapping[str, str]) -> None:
    file.write(f"{title}\n")
    for key, value in context.items():
        file.write(f"  {key + ':':<13} {value}\n")


def make_progress(
    total: int,
    *,
    json_output: bool = False,
    description: str = "Events calendar",
) -> ProgressReporter:
    """Return the bar in a terminal, or the log-only reporter otherwise."""
    if json_output or total <= 0 or not sys.stdout.isatty():
        return NullProgress()
    return TqdmProgress(total, description)

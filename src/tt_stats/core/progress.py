"""Transient progress display for long-running collectors.

Logging records what happened. This module owns the live display:

* a ``tqdm`` bar in a terminal, which overwrites its own line;
* clean one-line-per-item output when piped or when progress is disabled;
* nothing when JSON logs are requested, because those are the record already.
"""

import sys
from typing import Protocol, TextIO

from tqdm import tqdm


class ProgressReporter(Protocol):
    """Minimal interface for a progress display."""

    @property
    def logs_records(self) -> bool:
        """Whether the collector should also emit per-item log records."""
        ...

    def advance(self, message: str) -> None:
        """Mark one item complete and show what it was."""
        ...

    def close(self) -> None:
        """Finish the display."""
        ...


class NullProgress:
    """Used for JSON logs and library calls that own their own logging."""

    logs_records = True

    def advance(self, message: str) -> None:
        pass

    def close(self) -> None:
        pass


class PlainProgress:
    """One clear line per item, suitable for files and redirected output."""

    logs_records = False

    def __init__(
        self,
        total: int,
        description: str = "Events calendar",
        *,
        file: TextIO | None = None,
    ) -> None:
        self._total = total
        self._description = description
        self._file = file or sys.stdout
        self._completed = 0

    def advance(self, message: str) -> None:
        self._completed += 1
        self._file.write(
            f"{self._description}  {self._completed}/{self._total}  {message}\n"
        )
        self._file.flush()

    def close(self) -> None:
        pass


class TqdmProgress:
    """Terminal progress bar that overwrites its own line."""

    logs_records = False

    def __init__(
        self,
        total: int,
        description: str = "Events calendar",
        *,
        file: TextIO | None = None,
    ) -> None:
        self._bar = tqdm(
            total=total,
            desc=description,
            unit="year",
            file=file or sys.stderr,
            dynamic_ncols=True,
            leave=True,
        )

    def advance(self, message: str) -> None:
        self._bar.update(1)
        self._bar.set_postfix_str(message, refresh=True)

    def close(self) -> None:
        self._bar.close()


def make_progress(
    total: int,
    *,
    enabled: bool,
    json_output: bool = False,
    description: str = "Events calendar",
) -> ProgressReporter:
    """Return the right display for the current output mode."""
    if json_output or total <= 0:
        return NullProgress()
    if not enabled or not sys.stdout.isatty():
        return PlainProgress(total, description)
    return TqdmProgress(total, description)

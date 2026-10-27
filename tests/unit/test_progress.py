"""Unit tests for the terminal progress bar and its fallbacks."""

import io
import sys

import pytest

from tt_stats.core.progress import (
    NullProgress,
    TqdmProgress,
    make_progress,
)


def test_null_progress_is_a_noop() -> None:
    progress = NullProgress()
    progress.start("Starting events scrape", {"Total Years": "1"})
    progress.advance("year=2023")
    progress.finish("Events scrape complete", {"Failed": "0"})
    progress.close()


def test_json_output_returns_null() -> None:
    assert isinstance(make_progress(6, json_output=True), NullProgress)


def test_piped_output_returns_null(monkeypatch: pytest.MonkeyPatch) -> None:
    class NotATerminal(io.StringIO):
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(sys, "stdout", NotATerminal())

    assert isinstance(make_progress(6), NullProgress)


def test_terminal_output_returns_a_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdout", Terminal())
    monkeypatch.setattr(sys, "stderr", Terminal())

    progress = make_progress(6)
    assert isinstance(progress, TqdmProgress)
    progress.close()


def test_tqdm_progress_writes_a_compact_bar() -> None:
    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    output = Terminal()
    progress = TqdmProgress(2, file=output, out=output)

    progress.start("Starting events scrape", {"Total Years": "2"})
    progress.advance("year=2023 | saved=101 | api=101 | ok")
    progress.advance("year=2024 | saved=131 | api=131 | ok")
    progress.close()
    progress.finish(
        "Events scrape complete",
        {"Total Years": "2", "Failed": "0"},
    )

    rendered = output.getvalue()
    assert "Starting events scrape" in rendered
    assert "Events calendar" in rendered
    assert "year=2023" in rendered
    assert "[##########]" in rendered
    assert "] year=2023" in rendered
    assert "], year=" not in rendered
    assert "Events scrape complete" in rendered


def test_progress_reports_its_output_mode() -> None:
    assert NullProgress.logs_records is True
    assert NullProgress.prints_summary is False
    assert TqdmProgress.logs_records is False
    assert TqdmProgress.prints_summary is True

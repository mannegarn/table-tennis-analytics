"""Unit tests for the terminal progress display and its fallbacks."""

import io
import sys

from tt_stats.core.progress import (
    NullProgress,
    PlainProgress,
    TqdmProgress,
    make_progress,
)


def test_null_progress_is_a_noop() -> None:
    progress = NullProgress()
    progress.advance("year=2023")
    progress.close()


def test_disabled_progress_returns_plain_lines() -> None:
    assert isinstance(make_progress(3, enabled=False), PlainProgress)


def test_piped_output_returns_plain_lines(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class NotATerminal(io.StringIO):
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(sys, "stdout", NotATerminal())

    assert isinstance(make_progress(3, enabled=True), PlainProgress)


def test_json_output_returns_null() -> None:
    progress = make_progress(3, enabled=True, json_output=True)

    assert isinstance(progress, NullProgress)


def test_terminal_output_returns_a_bar(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdout", Terminal())
    monkeypatch.setattr(sys, "stderr", Terminal())

    progress = make_progress(2, enabled=True)
    assert isinstance(progress, TqdmProgress)
    progress.close()


def test_plain_progress_writes_one_line_per_item() -> None:
    output = io.StringIO()
    progress = PlainProgress(2, file=output)

    progress.advance("year=2023  rows=101  api=101  ok")
    progress.advance("year=2024  rows=131  api=131  ok")
    progress.close()

    assert output.getvalue().splitlines() == [
        "Events calendar  1/2  year=2023  rows=101  api=101  ok",
        "Events calendar  2/2  year=2024  rows=131  api=131  ok",
    ]


def test_tqdm_progress_writes_the_description_and_messages() -> None:
    output = io.StringIO()
    progress = TqdmProgress(2, file=output)

    progress.advance("year=2023 rows=101 ok")
    progress.advance("year=2024 rows=131 ok")
    progress.close()

    rendered = output.getvalue()
    assert "Events calendar" in rendered
    assert "year=2023" in rendered


def test_progress_reports_whether_it_logs_records() -> None:
    assert NullProgress.logs_records is True
    assert PlainProgress.logs_records is False
    assert TqdmProgress.logs_records is False

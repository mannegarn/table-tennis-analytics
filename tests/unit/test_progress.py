"""Unit tests for the progress bar helpers. No logging handlers are involved."""

import io
from datetime import UTC, datetime, timedelta

from tt_stats.core.progress import (
    RunTiming,
    format_duration,
    plural,
    progress_bar,
    track,
)


class Terminal(io.StringIO):
    """A stream that claims to be a terminal, so the bar renders into it."""

    def isatty(self) -> bool:
        return True


def test_progress_bar_rewrites_one_line() -> None:
    output = Terminal()

    with progress_bar(
        2, description="Collecting calendar", unit="year", file=output
    ) as bar:
        bar.set_postfix_str("year=2023", refresh=False)
        bar.update(1)
        bar.set_postfix_str("year=2024", refresh=False)
        bar.update(1)

    rendered = output.getvalue()
    assert "Collecting calendar" in rendered
    assert "2/2" in rendered
    assert "[##########]" in rendered
    assert "year=2024" in rendered
    # Rate and elapsed/remaining are part of the line, not just the counters.
    assert "year/s" in rendered
    assert "<" in rendered
    # Every frame was a carriage return; only the final frame ends the line.
    assert rendered.count("\n") == 1


def test_progress_bar_advances_by_chunk() -> None:
    output = Terminal()

    with progress_bar(40, description="Matches", unit="match", file=output) as bar:
        bar.update(20)
        bar.update(20)

    rendered = output.getvalue()
    assert "40/40" in rendered
    assert "[##########]" in rendered


def test_progress_bar_is_silent_outside_a_terminal() -> None:
    output = io.StringIO()

    with progress_bar(2, description="Collecting calendar", file=output) as bar:
        bar.update(2)

    assert output.getvalue() == ""


def test_progress_bar_can_be_disabled_explicitly() -> None:
    output = Terminal()

    with progress_bar(
        2, description="Collecting calendar", file=output, disable=True
    ) as bar:
        bar.update(2)

    assert output.getvalue() == ""


def test_track_advances_once_per_item() -> None:
    output = Terminal()

    seen = list(
        track([2023, 2024], description="Collecting calendar", unit="year", file=output)
    )

    assert seen == [2023, 2024]
    assert "2/2" in output.getvalue()


def test_format_duration_uses_the_largest_useful_unit() -> None:
    assert format_duration(timedelta(seconds=0)) == "0.0s"
    assert format_duration(timedelta(milliseconds=700)) == "0.7s"
    assert format_duration(timedelta(seconds=42)) == "42s"
    assert format_duration(timedelta(minutes=7, seconds=12)) == "7m 12s"
    assert format_duration(timedelta(hours=1, minutes=3, seconds=9)) == "1h 03m"


def test_plural_uses_the_singular_only_for_one() -> None:
    assert plural(0, "event") == "events"
    assert plural(1, "event") == "event"
    assert plural(2, "event") == "events"


def test_format_duration_clamps_a_negative_span() -> None:
    """A clock adjustment must not print a negative duration."""
    assert format_duration(timedelta(seconds=-5)) == "0.0s"


def test_run_timing_describes_elapsed_time_and_rate() -> None:
    start = datetime(2026, 11, 10, 20, 0, 0, tzinfo=UTC)
    timing = RunTiming(start, start + timedelta(seconds=100))

    assert timing.elapsed == timedelta(seconds=100)
    assert timing.rate_per_second(400) == 4.0
    assert timing.describe(400, unit="event") == "400 events in 1m 40s (4.0/s)"


def test_run_timing_handles_an_instant_run() -> None:
    start = datetime(2026, 11, 10, 20, 0, 0, tzinfo=UTC)
    timing = RunTiming(start, start)

    assert timing.rate_per_second(5) == 0.0
    assert timing.describe(5, unit="year") == "5 years in 0.0s (0.0/s)"
    assert timing.describe(1, unit="year") == "1 year in 0.0s (0.0/s)"

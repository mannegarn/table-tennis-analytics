"""Unit tests for the progress bar helpers. No logging handlers are involved."""

import io

from tt_stats.core.progress import progress_bar, track


class Terminal(io.StringIO):
    """A stream that claims to be a terminal, so the bar renders into it."""

    def isatty(self) -> bool:
        return True


def test_progress_bar_rewrites_one_line() -> None:
    output = Terminal()

    with progress_bar(
        2, description="Scraping calendar", unit="year", file=output
    ) as bar:
        bar.set_postfix_str("year=2023", refresh=False)
        bar.update(1)
        bar.set_postfix_str("year=2024", refresh=False)
        bar.update(1)

    rendered = output.getvalue()
    assert "Scraping calendar" in rendered
    assert "2/2" in rendered
    assert "[##########]" in rendered
    assert "year=2024" in rendered
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

    with progress_bar(2, description="Scraping calendar", file=output) as bar:
        bar.update(2)

    assert output.getvalue() == ""


def test_progress_bar_can_be_disabled_explicitly() -> None:
    output = Terminal()

    with progress_bar(
        2, description="Scraping calendar", file=output, disable=True
    ) as bar:
        bar.update(2)

    assert output.getvalue() == ""


def test_track_advances_once_per_item() -> None:
    output = Terminal()

    seen = list(
        track([2023, 2024], description="Scraping calendar", unit="year", file=output)
    )

    assert seen == [2023, 2024]
    assert "2/2" in output.getvalue()

"""Unit tests for the Markdown summary formatting helpers."""

from tt_stats.summary import _coverage_row, _fmt_int, _fmt_pair, _fmt_pct, _md_table


def test_fmt_int() -> None:
    assert _fmt_int(None) == "-"
    assert _fmt_int(0) == "0"
    assert _fmt_int(1234567) == "1,234,567"


def test_fmt_pct() -> None:
    assert _fmt_pct(0, 0) == "0.0%"
    assert _fmt_pct(1, 4) == "25.0%"
    assert _fmt_pct(2, 3) == "66.7%"


def test_fmt_pair() -> None:
    has_str, missing_str = _fmt_pair(75, 100)
    assert has_str == "75 (75.0%)"
    assert missing_str == "25 (25.0%)"


def test_coverage_row() -> None:
    row = _coverage_row("Events", 10, 4, notes="6 pending")
    assert row == ["Events", "10", "4 (40.0%)", "6 (60.0%)", "6 pending"]


def test_md_table() -> None:
    table = _md_table(["A", "B"], [["1", "2"], ["3", "4"]])
    assert table == "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |"

"""Unit tests for the shared SQL file loader. No database, no network."""

from pathlib import Path

import pytest

from tt_stats.core.sql_files import executable_sql, load_sql


def test_executable_sql_strips_line_and_block_comments() -> None:
    assert executable_sql("-- only a comment\n/* and a block */\n") == ""
    assert executable_sql("-- comment\nSELECT 1;\n") == "SELECT 1;"


def test_a_comment_only_file_is_a_scaffold(tmp_path: Path) -> None:
    path = tmp_path / "scaffold.sql"
    path.write_text("-- nothing yet\n/* nor here */\n", encoding="utf-8")

    assert load_sql(path) is None


def test_a_statement_is_returned_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "unpack.sql"
    path.write_text("-- comment\nSELECT 1;\n", encoding="utf-8")

    assert load_sql(path) == "-- comment\nSELECT 1;\n"


def test_a_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_sql(tmp_path / "absent.sql")

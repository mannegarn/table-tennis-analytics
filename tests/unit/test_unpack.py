"""Unit tests for the unpack SQL loader. No database, no network.

The guardrail tests skip themselves while ``unpack_events.sql`` is still a
scaffold of comments, and switch on automatically once the statement exists.
"""

from pathlib import Path

import pytest

from tt_stats.events.land.unpack import (
    UNPACK_SQL_PATH,
    executable_sql,
    load_unpack_sql,
)

STATEMENT = load_unpack_sql()

requires_statement = pytest.mark.skipif(
    STATEMENT is None,
    reason="unpack_events.sql is still a scaffold",
)


def test_executable_sql_strips_line_and_block_comments() -> None:
    assert executable_sql("-- only a comment\n/* and a block */\n") == ""
    assert executable_sql("-- comment\nSELECT 1;\n") == "SELECT 1;"


def test_unpack_file_exists() -> None:
    assert UNPACK_SQL_PATH.is_file()


def test_scaffold_loads_as_none(tmp_path: Path) -> None:
    path = tmp_path / "scaffold.sql"
    path.write_text("-- nothing but a comment\n", encoding="utf-8")

    assert load_unpack_sql(path) is None


def test_real_statement_is_returned(tmp_path: Path) -> None:
    path = tmp_path / "unpack.sql"
    path.write_text("-- comment\nSELECT 1;\n", encoding="utf-8")

    assert load_unpack_sql(path) == "-- comment\nSELECT 1;\n"


@requires_statement
def test_statement_is_a_single_dml_insert() -> None:
    body = executable_sql(STATEMENT or "")

    assert "ingest.events_unpacked" in body
    assert body.upper().startswith("INSERT"), "expected one INSERT statement"
    for forbidden in ("CREATE", "ALTER", "DROP", "TRUNCATE"):
        assert forbidden not in body.upper(), f"{forbidden} does not belong here"


@requires_statement
def test_statement_takes_exactly_one_parameter() -> None:
    body = executable_sql(STATEMENT or "")

    assert body.count("%s") == 1, "the year is the only parameter"


@requires_statement
def test_statement_deduplicates_and_upserts() -> None:
    body = executable_sql(STATEMENT or "").upper()

    assert "DISTINCT ON" in body, "2021 repeats events, so de-duplicate"
    assert "ON CONFLICT" in body, "re-running a year must update, not duplicate"


@requires_statement
def test_statement_has_no_tier_or_category_filter() -> None:
    body = executable_sql(STATEMENT or "").upper()

    # A WHERE on the year is expected and allowed; filtering on tier or event
    # type is not, because that belongs in dbt (ADR 0003).
    for forbidden in ("EVENT_TIER_NAME", "EVENTTYPE"):
        assert forbidden not in body, (
            f"{forbidden} suggests business filtering; that belongs in dbt"
        )

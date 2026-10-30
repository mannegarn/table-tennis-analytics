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

    assert body.count("%(year)s") == 1, "the year is the only parameter"
    assert "%s" not in body, "the year is bound by name, not position"


@requires_statement
def test_statement_upserts_on_the_composite_key() -> None:
    body = " ".join(executable_sql(STATEMENT or "").upper().split())

    assert "ON CONFLICT (SOURCE_YEAR, EVENT_ID)" in body, (
        "a tournament can appear in two years, so the year is part of the key"
    )
    assert "SOURCE_YEAR = EXCLUDED.SOURCE_YEAR" not in body, (
        "the year is key material now, not a value to overwrite"
    )


@requires_statement
def test_statement_tracks_presence_without_rewriting_first_seen() -> None:
    body = " ".join(executable_sql(STATEMENT or "").upper().split())

    assert "FIRST_SEEN_AT" in body, "presence needs a first sighting"
    assert "LAST_SEEN_AT = EXCLUDED.LAST_SEEN_AT" in body, (
        "every unpack must advance last_seen_at"
    )
    assert "FIRST_SEEN_AT = EXCLUDED.FIRST_SEEN_AT" not in body, (
        "first_seen_at must be written once and then left alone"
    )
    assert "REFRESHED_AT" not in body, "refreshed_at was replaced by last_seen_at"


@requires_statement
def test_statement_orders_by_event_then_start_date() -> None:
    body = " ".join(executable_sql(STATEMENT or "").upper().split())

    assert "ORDER BY (E.VALUE ->> 'EVENTID')::BIGINT" in body
    assert "(E.VALUE ->> 'STARTDATETIME')::TEXT DESC NULLS LAST" in body, (
        "a stable tie-breaker keeps the DISTINCT ON pick deterministic"
    )


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

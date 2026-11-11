"""Unit tests for the match-entries unpack statement. No database, no network.

The guardrail tests read the committed SQL as text and assert the decisions the
measurements forced (ADR 0010): the key is the event plus the document code, and
the upstream id and start time stay in the payload instead of becoming columns.
"""

from pathlib import Path

import pytest

from tt_stats.core.sql_files import executable_sql, load_sql
from tt_stats.matches.land import unpack

STATEMENT = unpack.load_unpack_sql()

requires_statement = pytest.mark.skipif(
    STATEMENT is None,
    reason="unpack_match_entries.sql is still a scaffold",
)


def _body() -> str:
    """The statement with comments stripped and whitespace collapsed."""
    statement = load_sql(unpack.UNPACK_SQL_PATH)
    assert statement is not None
    return " ".join(executable_sql(statement).split())


def _upper() -> str:
    return _body().upper()


@requires_statement
def test_statement_is_a_single_dml_insert() -> None:
    body = _upper()

    assert "INGEST.MATCH_ENTRIES_UNPACKED" in body
    assert body.startswith("INSERT"), "expected one INSERT statement"
    for forbidden in ("CREATE", "ALTER", "DROP", "TRUNCATE", "DELETE"):
        assert forbidden not in body, f"{forbidden} does not belong here"


@requires_statement
def test_statement_takes_exactly_one_parameter() -> None:
    body = _body()

    assert body.count("%(event_id)s") == 1, "the event is the only parameter"
    assert "%s" not in body, "the event is bound by name, not position"


@requires_statement
def test_statement_upserts_on_the_event_and_document_code() -> None:
    body = _upper()

    assert "ON CONFLICT (EVENT_ID, DOCUMENT_CODE)" in body, (
        "a match id repeats inside one event, so the document code is the key"
    )
    assert "EVENT_ID = EXCLUDED.EVENT_ID" not in body
    assert "DOCUMENT_CODE = EXCLUDED.DOCUMENT_CODE" not in body


@requires_statement
def test_statement_leaves_the_id_and_start_time_in_the_payload() -> None:
    body = _body()

    assert "->> 'id'" not in body, (
        "the id is neither unique nor global, and nothing reads it"
    )
    assert "->> 'iD'" not in body, "the iD spelling is payload-only too"
    assert "->> 'startDateLocal'" not in body, (
        "424 rows send no start time and 1665 send a sentinel; dbt owns that rule"
    )


@requires_statement
def test_statement_does_not_copy_the_constant_or_derivable_fields() -> None:
    """``fullResults`` is always OFFICIAL and ``subEventType`` repeats the code."""
    body = _body()

    assert "->> 'fullResults'" not in body, (
        "a constant on every measured row carries no signal"
    )
    assert "->> 'subEventType'" not in body, (
        "the label is derivable from documentCode; dbt owns that split"
    )


@requires_statement
def test_statement_deduplicates_on_the_key() -> None:
    body = _upper()

    assert "DISTINCT ON (R.EVENT_ID, (E.VALUE ->> 'DOCUMENTCODE'))" in body, (
        "insurance against a repeated document code inside one payload"
    )
    assert "ORDER BY R.EVENT_ID, (E.VALUE ->> 'DOCUMENTCODE')" in body, (
        "DISTINCT ON requires a matching ORDER BY prefix"
    )


@requires_statement
def test_statement_tracks_presence_without_rewriting_first_seen() -> None:
    body = _upper()

    assert "FIRST_SEEN_AT" in body, "presence needs a first sighting"
    assert "LAST_SEEN_AT = EXCLUDED.LAST_SEEN_AT" in body, (
        "every unpack must advance last_seen_at"
    )
    assert "FIRST_SEEN_AT = EXCLUDED.FIRST_SEEN_AT" not in body, (
        "first_seen_at must be written once and then left alone"
    )


@requires_statement
def test_statement_has_no_business_filter() -> None:
    where = _upper().split(" WHERE ", 1)[1].split(" ORDER BY ", 1)[0]

    for forbidden in ("SUB_EVENT_TYPE", "SUB_EVENTTYPE", "FULL_RESULTS"):
        assert forbidden not in where, (
            f"{forbidden} in the WHERE clause is business filtering; dbt owns that"
        )


def test_main_refuses_to_run_while_the_statement_is_a_scaffold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scaffold = tmp_path / "scaffold.sql"
    scaffold.write_text("-- nothing but a comment\n", encoding="utf-8")
    monkeypatch.setattr(unpack, "UNPACK_SQL_PATH", scaffold)

    assert unpack.main([]) == 1

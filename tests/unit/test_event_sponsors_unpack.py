"""Unit tests for the event_sponsors unpack statement. No database, no network."""

import pytest

from tt_stats.core.sql_files import executable_sql, load_sql
from tt_stats.event_sponsors.land import unpack

STATEMENT = unpack.load_unpack_sql()

requires_statement = pytest.mark.skipif(
    STATEMENT is None,
    reason="unpack_event_sponsors.sql is still a scaffold",
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

    assert "INGEST.EVENT_SPONSORS_UNPACKED" in body
    assert body.startswith("INSERT"), "expected one INSERT statement"
    for forbidden in ("CREATE", "ALTER", "DROP", "TRUNCATE", "DELETE"):
        assert forbidden not in body, f"{forbidden} does not belong here"


@requires_statement
def test_statement_takes_exactly_one_parameter() -> None:
    body = _body()

    assert body.count("%(event_id)s") == 1, "the event is the only parameter"
    assert "%s" not in body, "the event is bound by name, not position"


@requires_statement
def test_statement_upserts_on_event_and_sponsor_id() -> None:
    body = _upper()

    assert "ON CONFLICT (EVENT_ID, EVENT_SPONSOR_ID)" in body
    assert "EVENT_ID = EXCLUDED.EVENT_ID" not in body
    assert "EVENT_SPONSOR_ID = EXCLUDED.EVENT_SPONSOR_ID" not in body


@requires_statement
def test_statement_tracks_presence_without_rewriting_first_seen() -> None:
    body = _upper()

    assert "FIRST_SEEN_AT" in body
    assert "LAST_SEEN_AT = EXCLUDED.LAST_SEEN_AT" in body
    assert "FIRST_SEEN_AT = EXCLUDED.FIRST_SEEN_AT" not in body

"""Integration test: ``ingest.events_unpacked`` exists and enforces its contract.

Schema, constraints and upsert behaviour only. The unpack statement and its
placement inside the landing transaction are covered in ``test_events_unpack``.
Runs against the isolated ``tt_stats_test`` database, which is truncated around
every test, so fixture rows need no manual cleanup.
"""

from __future__ import annotations

from typing import Any

import pytest
from psycopg import Connection, errors

pytestmark = pytest.mark.integration

TEST_EVENT_ID = 1_234_567

# column_name: (data_type, is_nullable, fragment expected in the default)
EXPECTED_COLUMNS: dict[str, tuple[str, str, str | None]] = {
    "event_id": ("bigint", "NO", None),
    "event_name": ("text", "NO", None),
    "start_date": ("date", "NO", None),
    "end_date": ("date", "NO", None),
    "source_year": ("smallint", "NO", None),
    "refreshed_at": ("timestamp with time zone", "NO", "now()"),
}

INSERT = """
INSERT INTO ingest.events_unpacked (
    event_id, event_name, start_date, end_date, source_year
)
VALUES (%s, %s, %s, %s, %s);
"""


def test_events_unpacked_has_the_agreed_columns(
    db_connection: Connection[Any],
) -> None:
    columns = {
        row["column_name"]: row
        for row in db_connection.execute(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'ingest' AND table_name = 'events_unpacked';
            """
        ).fetchall()
    }
    primary_key = db_connection.execute(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON kcu.constraint_name = tc.constraint_name
         AND kcu.table_schema = tc.table_schema
        WHERE tc.table_schema = 'ingest'
          AND tc.table_name = 'events_unpacked'
          AND tc.constraint_type = 'PRIMARY KEY';
        """
    ).fetchall()

    assert set(columns) == set(EXPECTED_COLUMNS)
    for name, (data_type, nullable, default_fragment) in EXPECTED_COLUMNS.items():
        assert columns[name]["data_type"] == data_type, name
        assert columns[name]["is_nullable"] == nullable, name
        if default_fragment is None:
            assert columns[name]["column_default"] is None, name
        else:
            assert default_fragment in (columns[name]["column_default"] or ""), name

    assert [row["column_name"] for row in primary_key] == ["event_id"]


def test_events_unpacked_defaults_refreshed_at(
    db_connection: Connection[Any],
) -> None:
    db_connection.execute(
        INSERT,
        (TEST_EVENT_ID, "Test Event", "2023-01-10", "2023-01-15", 2023),
    )
    row = db_connection.execute(
        "SELECT refreshed_at FROM ingest.events_unpacked WHERE event_id = %s;",
        (TEST_EVENT_ID,),
    ).fetchone()

    assert row is not None
    assert row["refreshed_at"] is not None


def test_events_unpacked_upserts_in_place_by_event_id(
    db_connection: Connection[Any],
) -> None:
    upsert = """
        INSERT INTO ingest.events_unpacked (
            event_id, event_name, start_date, end_date, source_year
        )
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (event_id) DO UPDATE SET
            event_name = EXCLUDED.event_name,
            start_date = EXCLUDED.start_date,
            end_date = EXCLUDED.end_date,
            source_year = EXCLUDED.source_year,
            refreshed_at = now();
    """

    db_connection.execute(
        upsert,
        (TEST_EVENT_ID, "First Name", "2023-01-10", "2023-01-15", 2023),
    )
    db_connection.execute(
        upsert,
        (TEST_EVENT_ID, "Second Name", "2023-02-01", "2023-02-05", 2024),
    )
    rows = db_connection.execute(
        """
        SELECT event_name, start_date, end_date, source_year
        FROM ingest.events_unpacked
        WHERE event_id = %s;
        """,
        (TEST_EVENT_ID,),
    ).fetchall()

    assert len(rows) == 1
    assert rows[0]["event_name"] == "Second Name"
    assert str(rows[0]["start_date"]) == "2023-02-01"
    assert str(rows[0]["end_date"]) == "2023-02-05"
    assert rows[0]["source_year"] == 2024


@pytest.mark.parametrize(
    ("event_name", "start_date", "end_date", "source_year"),
    [
        ("   ", "2023-01-10", "2023-01-15", 2023),
        ("Inverted dates", "2023-01-15", "2023-01-10", 2023),
        ("Year out of range", "2023-01-10", "2023-01-15", 1999),
    ],
)
def test_events_unpacked_rejects_a_bad_row(
    db_connection: Connection[Any],
    event_name: str,
    start_date: str,
    end_date: str,
    source_year: int,
) -> None:
    with pytest.raises(errors.CheckViolation), db_connection:
        db_connection.execute(
            INSERT,
            (TEST_EVENT_ID, event_name, start_date, end_date, source_year),
        )


def test_events_unpacked_requires_a_name(
    db_connection: Connection[Any],
) -> None:
    with pytest.raises(errors.NotNullViolation), db_connection:
        db_connection.execute(
            """
            INSERT INTO ingest.events_unpacked (
                event_id, event_name, start_date, end_date, source_year
            )
            VALUES (%s, NULL, %s, %s, %s);
            """,
            (TEST_EVENT_ID, "2023-01-10", "2023-01-15", 2023),
        )

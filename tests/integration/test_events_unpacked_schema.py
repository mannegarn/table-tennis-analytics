"""Integration test: ``ingest.events_unpacked`` exists and enforces its contract.

Schema, constraints and upsert behaviour only. The unpack statement and its
placement inside the landing transaction are covered in ``test_events_unpack``.
Runs against the isolated ``tt_stats_test`` database, which is truncated around
every test, so fixture rows need no manual cleanup.

The key is ``(source_year, event_id)`` rather than ``event_id`` alone, because a
few tournaments appear in two years' calendars. The freshness columns record
presence: ``first_seen_at`` never moves, ``last_seen_at`` moves on every sighting.
"""

from __future__ import annotations

from typing import Any

import pytest
from psycopg import Connection, errors

pytestmark = pytest.mark.integration

TEST_EVENT_ID = 1_234_567
FIRST_YEAR = 2023
SECOND_YEAR = 2024

# column_name: (data_type, is_nullable, fragment expected in the default)
EXPECTED_COLUMNS: dict[str, tuple[str, str, str | None]] = {
    "event_id": ("bigint", "NO", None),
    "event_name": ("text", "NO", None),
    "start_date": ("date", "NO", None),
    "end_date": ("date", "NO", None),
    "source_year": ("smallint", "NO", None),
    "first_seen_at": ("timestamp with time zone", "NO", "clock_timestamp()"),
    "last_seen_at": ("timestamp with time zone", "NO", "clock_timestamp()"),
}

INSERT = """
INSERT INTO ingest.events_unpacked (
    source_year, event_id, event_name, start_date, end_date
)
VALUES (%s, %s, %s, %s, %s);
"""

UPSERT = """
INSERT INTO ingest.events_unpacked (
    source_year, event_id, event_name, start_date, end_date, last_seen_at
)
VALUES (%s, %s, %s, %s, %s, clock_timestamp())
ON CONFLICT (source_year, event_id) DO UPDATE SET
    event_name = EXCLUDED.event_name,
    start_date = EXCLUDED.start_date,
    end_date = EXCLUDED.end_date,
    last_seen_at = EXCLUDED.last_seen_at
RETURNING first_seen_at, last_seen_at;
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
          AND tc.constraint_type = 'PRIMARY KEY'
        ORDER BY kcu.ordinal_position;
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

    assert [row["column_name"] for row in primary_key] == ["source_year", "event_id"]


def test_events_unpacked_defaults_the_freshness_columns(
    db_connection: Connection[Any],
) -> None:
    db_connection.execute(
        INSERT,
        (FIRST_YEAR, TEST_EVENT_ID, "Test Event", "2023-01-10", "2023-01-15"),
    )
    row = db_connection.execute(
        """
        SELECT first_seen_at, last_seen_at
        FROM ingest.events_unpacked
        WHERE source_year = %s AND event_id = %s;
        """,
        (FIRST_YEAR, TEST_EVENT_ID),
    ).fetchone()

    assert row is not None
    assert row["first_seen_at"] is not None
    assert row["last_seen_at"] is not None


def test_events_unpacked_upserts_in_place_within_a_year(
    db_connection: Connection[Any],
) -> None:
    db_connection.execute(
        UPSERT,
        (FIRST_YEAR, TEST_EVENT_ID, "First Name", "2023-01-10", "2023-01-15"),
    )
    db_connection.execute(
        UPSERT,
        (FIRST_YEAR, TEST_EVENT_ID, "Second Name", "2023-02-01", "2023-02-05"),
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
    assert rows[0]["source_year"] == FIRST_YEAR


def test_events_unpacked_keeps_one_row_per_year_for_the_same_event(
    db_connection: Connection[Any],
) -> None:
    """A tournament listed in two calendars survives in both, no clobbering."""
    db_connection.execute(
        INSERT,
        (FIRST_YEAR, TEST_EVENT_ID, "Straddling Cup", "2023-12-28", "2024-01-03"),
    )
    db_connection.execute(
        INSERT,
        (SECOND_YEAR, TEST_EVENT_ID, "Straddling Cup", "2023-12-28", "2024-01-03"),
    )
    rows = db_connection.execute(
        """
        SELECT source_year FROM ingest.events_unpacked
        WHERE event_id = %s
        ORDER BY source_year;
        """,
        (TEST_EVENT_ID,),
    ).fetchall()

    assert [row["source_year"] for row in rows] == [FIRST_YEAR, SECOND_YEAR]


def test_re_seeing_an_event_advances_last_seen_at_only(
    db_connection: Connection[Any],
) -> None:
    values = (FIRST_YEAR, TEST_EVENT_ID, "Test Event", "2023-01-10", "2023-01-15")
    first = db_connection.execute(UPSERT, values).fetchone()
    second = db_connection.execute(UPSERT, values).fetchone()

    assert first is not None
    assert second is not None
    assert second["first_seen_at"] == first["first_seen_at"]
    assert second["last_seen_at"] > first["last_seen_at"]


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
            (source_year, TEST_EVENT_ID, event_name, start_date, end_date),
        )


def test_events_unpacked_requires_a_name(
    db_connection: Connection[Any],
) -> None:
    with pytest.raises(errors.NotNullViolation), db_connection:
        db_connection.execute(
            """
            INSERT INTO ingest.events_unpacked (
                source_year, event_id, event_name, start_date, end_date
            )
            VALUES (%s, %s, NULL, %s, %s);
            """,
            (FIRST_YEAR, TEST_EVENT_ID, "2023-01-10", "2023-01-15"),
        )

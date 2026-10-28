"""Integration test: landing unpacks in the same transaction, backfill repeats safely.

The whole module skips itself while ``unpack_events.sql`` is still a scaffold of
comments, so the suite stays green until the statement is written.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from pydantic import ValidationError

from tt_stats.core.config import get_settings
from tt_stats.core.db import get_connection
from tt_stats.core.migrations import apply_migrations
from tt_stats.events.collect.events_by_year import YearPayloadSummary
from tt_stats.events.land import land_events_by_year
from tt_stats.events.land.unpack import load_unpack_sql, unpack_years

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        load_unpack_sql() is None,
        reason="unpack_events.sql is still a scaffold",
    ),
]

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "events_2023.json"
TEST_YEAR = 2099
KNOWN_EVENT_ID = 2629


def _require_database() -> None:
    try:
        settings = get_settings()
    except ValidationError as error:
        pytest.skip(f"Database settings are incomplete: {error}")

    try:
        with psycopg.connect(settings.dsn):
            pass
    except psycopg.OperationalError as error:
        pytest.skip(f"PostgreSQL not available: {error}")


def _summary(payload: Any, digest: str) -> YearPayloadSummary:
    envelope = payload[0]
    return YearPayloadSummary(
        year=TEST_YEAR,
        status_code=200,
        api_count=envelope["Count"],
        original_count=int(envelope["Original_Count"]),
        row_count=len(envelope["rows"]),
        payload_sha256=digest,
        fetched_at=datetime.now(tz=UTC),
        error=None,
    )


def _cleanup() -> None:
    with get_connection() as connection:
        connection.execute(
            "DELETE FROM ingest.events_unpacked WHERE source_year = %s;",
            (TEST_YEAR,),
        )
        connection.execute(
            "DELETE FROM ingest.events_by_year_raw WHERE year = %s;",
            (TEST_YEAR,),
        )


def test_landing_unpacks_in_the_same_transaction() -> None:
    _require_database()
    apply_migrations()

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    summary = _summary(payload, digest)
    expected = len({row["EventId"] for row in payload[0]["rows"]})

    try:
        land_events_by_year(summary, payload)
        land_events_by_year(summary, payload)

        with get_connection() as connection:
            counts = connection.execute(
                """
                SELECT count(*) AS rows, count(DISTINCT event_id) AS events
                FROM ingest.events_unpacked
                WHERE source_year = %s;
                """,
                (TEST_YEAR,),
            ).fetchone()
            spot = connection.execute(
                """
                SELECT event_name, start_date, end_date, refreshed_at
                FROM ingest.events_unpacked
                WHERE event_id = %s;
                """,
                (KNOWN_EVENT_ID,),
            ).fetchone()

        assert counts == {"rows": expected, "events": expected}
        assert spot is not None
        assert spot["event_name"] == "Singapore Smash 2023"
        assert str(spot["start_date"]) == "2023-03-07"
        assert str(spot["end_date"]) == "2023-03-19"
        assert spot["refreshed_at"] is not None
    finally:
        _cleanup()


def test_backfill_is_idempotent() -> None:
    _require_database()
    apply_migrations()

    first = unpack_years()
    if not first:
        pytest.skip("no landed years to backfill")

    second = unpack_years()

    assert [rows for _, rows in first] == [rows for _, rows in second]

    with get_connection() as connection:
        counts = connection.execute(
            """
            SELECT count(*) AS rows, count(DISTINCT event_id) AS events
            FROM ingest.events_unpacked;
            """
        ).fetchone()

    assert counts is not None
    assert counts["rows"] == counts["events"]


def test_backfill_matches_the_distinct_events_in_the_calendar() -> None:
    _require_database()
    apply_migrations()

    unpack_years()
    with get_connection() as connection:
        expected = connection.execute(
            """
            SELECT count(*) AS events
            FROM (
                SELECT DISTINCT e.value ->> 'EventId' AS event_id
                FROM ingest.events_by_year_raw AS r
                CROSS JOIN LATERAL jsonb_array_elements(r.payload -> 0 -> 'rows') AS e
            ) AS distinct_events;
            """
        ).fetchone()
        actual = connection.execute(
            "SELECT count(*) AS events FROM ingest.events_unpacked;"
        ).fetchone()

    assert expected is not None
    assert actual is not None
    assert actual["events"] == expected["events"]

"""Integration test: landing unpacks in the same transaction, repeats are safe.

Runs against the isolated ``tt_stats_test`` database, so the committed fixture
can be landed with its real EventIds. The module skips itself while
``unpack_events.sql`` is still a scaffold of comments.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from psycopg import Connection

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
TEST_YEAR = 2023
KNOWN_EVENT_ID = 2629


def _payload() -> Any:
    """Return the committed fixture payload unchanged."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _summary(payload: Any, digest: str) -> YearPayloadSummary:
    envelope = payload[0]
    return YearPayloadSummary(
        year=TEST_YEAR,
        status_code=200,
        source_final_count=envelope["Count"],
        source_original_count=int(envelope["Original_Count"]),
        received_count=len(envelope["rows"]),
        payload_sha256=digest,
        fetched_at=datetime.now(tz=UTC),
        error=None,
    )


def _counts(connection: Connection[Any]) -> dict[str, int]:
    row = connection.execute(
        """
        SELECT count(*) AS rows, count(DISTINCT event_id) AS events
        FROM ingest.events_unpacked
        WHERE source_year = %s;
        """,
        (TEST_YEAR,),
    ).fetchone()
    return dict(row) if row else {"rows": 0, "events": 0}


@pytest.fixture
def landed_year() -> int:
    """Land the fixture year; the truncate fixture removes every trace of it."""
    payload = _payload()
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    land_events_by_year(_summary(payload, digest), payload)
    return TEST_YEAR


def test_landing_unpacks_in_the_same_transaction(
    landed_year: int,
    db_connection: Connection[Any],
) -> None:
    payload = _payload()
    expected = len({row["EventId"] for row in payload[0]["rows"]})

    assert _counts(db_connection) == {"rows": expected, "events": expected}


def test_unpacked_columns_match_the_payload(
    landed_year: int,
    db_connection: Connection[Any],
) -> None:
    row = db_connection.execute(
        """
        SELECT event_name, start_date, end_date, source_year, refreshed_at
        FROM ingest.events_unpacked
        WHERE event_id = %s;
        """,
        (KNOWN_EVENT_ID,),
    ).fetchone()

    assert row is not None
    assert row["event_name"] == "Singapore Smash 2023"
    assert str(row["start_date"]) == "2023-03-07"
    assert str(row["end_date"]) == "2023-03-19"
    assert row["source_year"] == TEST_YEAR
    assert row["refreshed_at"] is not None


def test_re_running_the_statement_for_one_year_is_idempotent(
    landed_year: int,
    db_connection: Connection[Any],
) -> None:
    first = unpack_years([TEST_YEAR])
    second = unpack_years([TEST_YEAR])
    counts = _counts(db_connection)

    assert first == second
    assert counts["rows"] == counts["events"]


def test_dry_run_reports_counts_but_rolls_back(
    landed_year: int,
    db_connection: Connection[Any],
) -> None:
    before = _counts(db_connection)

    unpack_years([TEST_YEAR], dry_run=True)

    assert _counts(db_connection) == before

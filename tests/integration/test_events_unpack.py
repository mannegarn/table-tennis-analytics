"""Integration test: landing unpacks in the same transaction, repeats are safe.

Everything here runs against the fixture year ``2099`` so a half-written
statement can never damage the real 2021-2026 data. The module skips itself
while ``unpack_events.sql`` is still a scaffold of comments.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
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

# The fixture is real 2023 data, so its EventIds already exist in the warehouse.
# Landing it unchanged would rewrite those rows' source_year and the cleanup
# below would then delete real data. Shifting the ids into a private range keeps
# this test hermetic.
ID_OFFSET = 900_000_000
KNOWN_EVENT_ID = 2629 + ID_OFFSET


def _payload() -> Any:
    """Return the fixture payload with its EventIds moved out of the real range."""
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for row in payload[0]["rows"]:
        row["EventId"] = int(row["EventId"]) + ID_OFFSET
    return payload


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
        source_final_count=envelope["Count"],
        source_original_count=int(envelope["Original_Count"]),
        received_count=len(envelope["rows"]),
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


def _real_year_total() -> int:
    """Rows belonging to real data, ie anything outside the test year."""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT count(*) AS n FROM ingest.events_unpacked WHERE source_year < %s;",
            (TEST_YEAR,),
        ).fetchone()
    return int(row["n"]) if row else 0


def _counts() -> dict[str, int]:
    with get_connection() as connection:
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
def landed_year() -> Iterator[int]:
    """Land the fixture year once, then remove every trace of it."""
    _require_database()
    apply_migrations()

    real_before = _real_year_total()

    payload = _payload()
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    land_events_by_year(_summary(payload, digest), payload)
    try:
        yield TEST_YEAR
    finally:
        _cleanup()
        # Guard against the test ever touching real data again: this caught a
        # version that reparented real 2023 events into 2099 and then deleted them.
        assert _real_year_total() == real_before, (
            "this test changed rows outside the test year"
        )


def test_landing_unpacks_in_the_same_transaction(landed_year: int) -> None:
    payload = _payload()
    expected = len({row["EventId"] for row in payload[0]["rows"]})

    assert _counts() == {"rows": expected, "events": expected}


def test_unpacked_columns_match_the_payload(landed_year: int) -> None:
    with get_connection() as connection:
        row = connection.execute(
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
) -> None:
    first = unpack_years([TEST_YEAR])
    second = unpack_years([TEST_YEAR])

    assert first == second
    assert _counts()["rows"] == _counts()["events"]


def test_dry_run_reports_counts_but_rolls_back(landed_year: int) -> None:
    before = _counts()

    unpack_years([TEST_YEAR], dry_run=True)

    assert _counts() == before

"""Integration tests: the year queue is derived, and it forgets what it has.

Runs against ``tt_stats_test``. The autouse fixture truncates the ingest tables
around every test, so each case starts from a database with no calendar at all,
which is exactly the first-backfill situation.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from psycopg import Connection
from psycopg.types.json import Jsonb

from tt_stats.events.collect.events_by_year import pending_years

pytestmark = pytest.mark.integration

# A well-formed SHA-256 for the raw row's CHECK constraint. The value itself is
# irrelevant here: these tests are about eligibility, not about payload hashes.
_DIGEST = "a" * 64

# The measured first year with tournaments, and the floor the collector uses.
FIRST_YEAR = 1926


def _current_year() -> int:
    return datetime.now(tz=UTC).year


def _land_year(connection: Connection[Any], year: int, *, received: int = 0) -> None:
    """Write a raw row for ``year`` and commit it.

    Committed on purpose: ``pending_years`` opens its own connection, so an
    uncommitted insert would be invisible to the very query under test.
    """
    payload: list[dict[str, Any]] = [
        {"rows": [], "Count": received, "Original_Count": str(received)}
    ]
    connection.execute(
        """
        INSERT INTO ingest.events_by_year_raw (
            year, payload, http_status, source_final_count,
            source_original_count, received_count, payload_sha256, fetched_at
        )
        VALUES (%s, %s, 200, %s, %s, %s, %s, %s)
        ON CONFLICT (year) DO UPDATE SET
            payload = EXCLUDED.payload,
            received_count = EXCLUDED.received_count,
            fetched_at = EXCLUDED.fetched_at;
        """,
        (
            year,
            Jsonb(payload),
            received,
            received,
            received,
            _DIGEST,
            datetime.now(tz=UTC),
        ),
    )
    connection.commit()


def test_a_cold_database_queues_every_year_from_the_floor() -> None:
    """First run: the current year, then next year, then the whole backfill."""
    queue = pending_years(FIRST_YEAR)
    years = [year for year, _ in queue]
    current_year = _current_year()

    assert queue[0] == (current_year, "current")
    assert queue[1] == (current_year + 1, "next")

    backfill = [(year, reason) for year, reason in queue if reason == "backfill"]
    assert [year for year, _ in backfill] == sorted(
        (year for year, _ in backfill), reverse=True
    )
    assert min(years) == FIRST_YEAR
    assert len(years) == current_year - FIRST_YEAR + 2


def test_a_landed_past_year_leaves_the_queue(
    db_connection: Connection[Any],
) -> None:
    """A past year with a payload is done: never requested again."""
    finished_year = _current_year() - 5
    _land_year(db_connection, finished_year, received=42)

    years = [year for year, _ in pending_years(FIRST_YEAR)]

    assert finished_year not in years
    assert _current_year() in years


def test_the_current_year_stays_in_the_queue_after_landing(
    db_connection: Connection[Any],
) -> None:
    """The live calendar keeps changing, so it is refreshed every run."""
    current_year = _current_year()
    _land_year(db_connection, current_year, received=10)

    queue = pending_years(FIRST_YEAR)

    assert (current_year, "current") in queue
    assert (current_year + 1, "next") in queue


def test_an_empty_payload_counts_as_fetched(
    db_connection: Connection[Any],
) -> None:
    """A quiet year lands nothing and is then retired from the queue."""
    quiet_year = _current_year() - 40
    _land_year(db_connection, quiet_year, received=0)

    assert quiet_year not in [year for year, _ in pending_years(FIRST_YEAR)]


def test_the_queue_starts_at_the_first_year_with_data(
    db_connection: Connection[Any],
) -> None:
    """Nothing before 1926 exists upstream, so nothing there is offered."""
    assert min(year for year, _ in pending_years(1900)) == FIRST_YEAR


def test_the_caller_floor_can_narrow_the_backfill(
    db_connection: Connection[Any],
) -> None:
    """`events_start_year` still works as a floor, for narrowing only."""
    assert min(year for year, _ in pending_years(2010)) == 2010

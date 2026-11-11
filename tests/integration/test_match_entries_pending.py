"""Integration tests: ``ingest.match_entries_pending`` derives the work queue.

Every case writes its own events and payloads, then reads the view. Runs against
``tt_stats_test``, truncated around each test by the autouse fixture.

The one-day grace is ``end_date + 1``. It is used by both the live test and the
stale test, so an event cannot be simultaneously "finished" and "possibly
fetched mid-tournament".
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from psycopg import Connection
from psycopg.types.json import Jsonb

from tt_stats.matches.collect.match_entries import pending_events

pytestmark = pytest.mark.integration

_DIGEST = "c" * 64


def _today() -> date:
    return datetime.now(tz=UTC).date()


def _add_event(
    connection: Connection[Any],
    event_id: int,
    *,
    start_offset: int,
    end_offset: int,
    source_year: int | None = None,
) -> None:
    """Insert an unpacked event relative to today, then commit it."""
    today = _today()
    connection.execute(
        """
        INSERT INTO ingest.events_unpacked (
            source_year, event_id, event_name, start_date, end_date
        )
        VALUES (%s, %s, %s, %s, %s);
        """,
        (
            today.year if source_year is None else source_year,
            event_id,
            f"Event {event_id}",
            today + timedelta(days=start_offset),
            today + timedelta(days=end_offset),
        ),
    )
    connection.commit()


def _add_payload(
    connection: Connection[Any],
    event_id: int,
    *,
    fetched_at: datetime,
    received: int = 5,
) -> None:
    """Insert a landed match list, then commit it."""
    connection.execute(
        """
        INSERT INTO ingest.match_entries_payloads_raw (
            event_id, payload, http_status, received_count,
            payload_sha256, fetched_at
        )
        VALUES (%s, %s, 200, %s, %s, %s);
        """,
        (event_id, Jsonb([{"id": 1}]), received, _DIGEST, fetched_at),
    )
    connection.commit()


def _queue(connection: Connection[Any]) -> list[tuple[int, str]]:
    rows = connection.execute(
        """
        SELECT event_id, reason, start_date, end_date
        FROM ingest.match_entries_pending
        ORDER BY
            (reason = 'live') DESC,
            GREATEST(start_date, end_date) DESC,
            event_id;
        """
    ).fetchall()
    return [(int(row["event_id"]), str(row["reason"])) for row in rows]


def _reason_for(connection: Connection[Any], event_id: int) -> str | None:
    for queued_id, reason in _queue(connection):
        if queued_id == event_id:
            return reason
    return None


def test_an_event_that_has_not_started_is_not_queued(
    db_connection: Connection[Any],
) -> None:
    _add_event(db_connection, 1, start_offset=5, end_offset=10)

    assert _reason_for(db_connection, 1) is None


def test_a_live_event_is_queued_without_a_payload(
    db_connection: Connection[Any],
) -> None:
    _add_event(db_connection, 2, start_offset=-3, end_offset=+2)

    assert _reason_for(db_connection, 2) == "live"


def test_a_live_event_is_refreshed_even_when_it_has_a_payload(
    db_connection: Connection[Any],
) -> None:
    _add_event(db_connection, 3, start_offset=-3, end_offset=+2)
    _add_payload(db_connection, 3, fetched_at=datetime.now(tz=UTC))

    assert _reason_for(db_connection, 3) == "live"


def test_the_day_after_the_end_date_still_counts_as_live(
    db_connection: Connection[Any],
) -> None:
    """The one-day grace, so a tournament ending yesterday is still refreshed."""
    _add_event(db_connection, 4, start_offset=-10, end_offset=-1)
    _add_payload(db_connection, 4, fetched_at=datetime.now(tz=UTC))

    assert _reason_for(db_connection, 4) == "live"


def test_a_finished_event_with_no_payload_is_new(
    db_connection: Connection[Any],
) -> None:
    _add_event(db_connection, 5, start_offset=-30, end_offset=-20)

    assert _reason_for(db_connection, 5) == "new"


def test_a_snapshot_taken_on_the_final_day_is_stale(
    db_connection: Connection[Any],
) -> None:
    """Fetched on the last day, so the later rounds may be missing."""
    end_date = _today() - timedelta(days=20)
    _add_event(db_connection, 6, start_offset=-30, end_offset=-20)
    fetched = datetime.combine(end_date, datetime.min.time(), tzinfo=UTC)
    _add_payload(db_connection, 6, fetched_at=fetched)

    assert _reason_for(db_connection, 6) == "stale"


def test_a_snapshot_taken_after_the_window_closed_is_done(
    db_connection: Connection[Any],
) -> None:
    _add_event(db_connection, 7, start_offset=-30, end_offset=-20)
    fetched = datetime.combine(
        _today() - timedelta(days=18), datetime.min.time(), tzinfo=UTC
    )
    _add_payload(db_connection, 7, fetched_at=fetched)

    assert _reason_for(db_connection, 7) is None


def test_live_events_come_before_the_backfill(
    db_connection: Connection[Any],
) -> None:
    """Ordering is priority: live first, then finished by most recent end."""
    _add_event(db_connection, 8, start_offset=-30, end_offset=-20)
    _add_event(db_connection, 9, start_offset=-3, end_offset=+1)
    _add_event(db_connection, 10, start_offset=-60, end_offset=-50)

    queue = _queue(db_connection)

    assert queue[0] == (9, "live")
    assert [event_id for event_id, _ in queue[1:]] == [8, 10]


def test_pending_events_returns_the_queue_and_applies_the_limit(
    db_connection: Connection[Any],
) -> None:
    """The CLI's queue reader: ordered pairs, and ``limit`` takes a prefix."""
    _add_event(db_connection, 11, start_offset=-30, end_offset=-20)
    _add_event(db_connection, 12, start_offset=-3, end_offset=+1)

    assert pending_events(db_connection) == [(12, "live"), (11, "new")]
    assert pending_events(db_connection, 1) == [(12, "live")]
    assert pending_events(db_connection, 0) == []


def test_an_event_in_two_calendars_is_queued_only_once(
    db_connection: Connection[Any],
) -> None:
    """A New Year straddling tournament appears in two years but is fetched once."""
    today = _today()
    _add_event(
        db_connection,
        13,
        start_offset=-3,
        end_offset=+2,
        source_year=today.year - 1,
    )
    _add_event(
        db_connection,
        13,
        start_offset=-3,
        end_offset=+2,
        source_year=today.year,
    )

    assert pending_events(db_connection) == [(13, "live")]


def test_an_inverted_date_range_does_not_expire_before_its_start_date(
    db_connection: Connection[Any],
) -> None:
    """Upstream sends end_date < start_date (e.g. 1992); live window uses GREATEST."""
    _add_event(db_connection, 14, start_offset=0, end_offset=-365)
    _add_payload(db_connection, 14, fetched_at=datetime.now(tz=UTC))

    assert _reason_for(db_connection, 14) == "live"

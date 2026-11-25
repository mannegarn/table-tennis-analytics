"""Integration tests: ``ingest_wtt.match_commentary_pending`` derives the work queue.

Every case writes its own events, match entries, payloads, and run history, then
reads the view or calls ``pending_matches``. Runs against ``tt_stats_test``,
truncated around each test by the autouse fixture.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from psycopg import Connection
from psycopg.types.json import Jsonb

from tt_stats.core.ingest_runs import ENDPOINT_WTT_MATCH_COMMENTARY
from tt_stats.matches.collect.match_commentary import pending_matches

pytestmark = pytest.mark.integration

_DIGEST = "f" * 64


def _today() -> date:
    return datetime.now(tz=UTC).date()


def _add_event(
    connection: Connection[Any],
    event_id: int,
    *,
    start_offset: int,
    end_offset: int,
    source_year: int | None = None,
    last_seen_at: datetime | None = None,
) -> None:
    """Insert an unpacked event relative to today."""
    today = _today()
    year = today.year if source_year is None else source_year
    start_date = today + timedelta(days=start_offset)
    end_date = today + timedelta(days=end_offset)
    if last_seen_at is None:
        connection.execute(
            """
            INSERT INTO ingest_wtt.events_unpacked (
                source_year, event_id, event_name, start_date, end_date
            )
            VALUES (%s, %s, %s, %s, %s);
            """,
            (year, event_id, f"Event {event_id}", start_date, end_date),
        )
    else:
        connection.execute(
            """
            INSERT INTO ingest_wtt.events_unpacked (
                source_year, event_id, event_name, start_date, end_date,
                last_seen_at
            )
            VALUES (%s, %s, %s, %s, %s, %s);
            """,
            (
                year,
                event_id,
                f"Event {event_id}",
                start_date,
                end_date,
                last_seen_at,
            ),
        )


def _add_match_entry(
    connection: Connection[Any],
    event_id: int,
    document_code: str = "TTEMSINGLES-----------FNL-000100----------",
) -> None:
    """Insert an unpacked match entry."""
    connection.execute(
        """
        INSERT INTO ingest_wtt.match_entries_unpacked (event_id, document_code)
        VALUES (%s, %s);
        """,
        (event_id, document_code),
    )


def _add_payload(
    connection: Connection[Any],
    event_id: int,
    document_code: str = "TTEMSINGLES-----------FNL-000100----------",
    *,
    fetched_at: datetime,
    received: int = 12,
) -> None:
    """Insert a landed match_commentary payload."""
    connection.execute(
        """
        INSERT INTO ingest_wtt.match_commentary_raw (
            event_id, document_code, payload, http_status, received_count,
            payload_sha256, fetched_at
        )
        VALUES (%s, %s, %s, 200, %s, %s, %s);
        """,
        (
            event_id,
            document_code,
            Jsonb([{"line": 1}]),
            received,
            _DIGEST,
            fetched_at,
        ),
    )


def _add_run(
    connection: Connection[Any],
    event_id: int,
    document_code: str,
    *,
    status: str,
    finished_at: datetime,
) -> None:
    """Insert an ingest_runs row for ``wtt_match_commentary``."""
    connection.execute(
        """
        INSERT INTO ingest_wtt.ingest_runs (
            endpoint, entity_key, status, started_at, finished_at, error
        )
        VALUES (%s, %s, %s, %s, %s, %s);
        """,
        (
            ENDPOINT_WTT_MATCH_COMMENTARY,
            f"{event_id}:{document_code}",
            status,
            finished_at - timedelta(seconds=1),
            finished_at,
            "upstream error" if status == "failed" else None,
        ),
    )


def _row_for(
    connection: Connection[Any],
    event_id: int,
    document_code: str = "TTEMSINGLES-----------FNL-000100----------",
) -> tuple[str, int | None] | None:
    row = connection.execute(
        """
        SELECT reason, previous_received_count
        FROM ingest_wtt.match_commentary_pending
        WHERE event_id = %s AND document_code = %s;
        """,
        (event_id, document_code),
    ).fetchone()
    if row is None:
        return None
    prev = row["previous_received_count"]
    return (str(row["reason"]), None if prev is None else int(prev))


def _reason_for(
    connection: Connection[Any],
    event_id: int,
    document_code: str = "TTEMSINGLES-----------FNL-000100----------",
) -> str | None:
    row = _row_for(connection, event_id, document_code)
    return None if row is None else row[0]


def test_a_match_for_a_future_event_is_not_queued(
    db_connection: Connection[Any],
) -> None:
    _add_event(db_connection, 1, start_offset=5, end_offset=10)
    _add_match_entry(db_connection, 1)

    assert _reason_for(db_connection, 1) is None


def test_a_live_event_match_is_queued_without_and_with_payload(
    db_connection: Connection[Any],
) -> None:
    _add_event(db_connection, 2, start_offset=-3, end_offset=2)
    _add_match_entry(db_connection, 2)

    assert _row_for(db_connection, 2) == ("live", None)

    _add_payload(
        db_connection,
        2,
        fetched_at=datetime.now(tz=UTC),
        received=17,
    )

    assert _row_for(db_connection, 2) == ("live", 17)


def test_the_day_after_the_end_date_still_counts_as_live(
    db_connection: Connection[Any],
) -> None:
    """The one-day grace window keeps yesterday's finished event live."""
    _add_event(db_connection, 4, start_offset=-10, end_offset=-1)
    _add_match_entry(db_connection, 4)
    _add_payload(db_connection, 4, fetched_at=datetime.now(tz=UTC), received=9)

    assert _row_for(db_connection, 4) == ("live", 9)


def test_a_finished_event_match_with_no_payload_is_new(
    db_connection: Connection[Any],
) -> None:
    _add_event(db_connection, 5, start_offset=-30, end_offset=-20)
    _add_match_entry(db_connection, 5)

    assert _row_for(db_connection, 5) == ("new", None)


def test_a_snapshot_taken_on_the_final_day_is_stale_and_carries_previous_count(
    db_connection: Connection[Any],
) -> None:
    end_date = _today() - timedelta(days=20)
    _add_event(db_connection, 6, start_offset=-30, end_offset=-20)
    _add_match_entry(db_connection, 6)
    fetched = datetime.combine(end_date, datetime.min.time(), tzinfo=UTC)
    _add_payload(db_connection, 6, fetched_at=fetched, received=42)

    assert _row_for(db_connection, 6) == ("stale", 42)


def test_a_snapshot_taken_after_the_window_closed_is_done(
    db_connection: Connection[Any],
) -> None:
    _add_event(db_connection, 7, start_offset=-30, end_offset=-20)
    _add_match_entry(db_connection, 7)
    fetched = datetime.combine(
        _today() - timedelta(days=18), datetime.min.time(), tzinfo=UTC
    )
    _add_payload(db_connection, 7, fetched_at=fetched)

    assert _reason_for(db_connection, 7) is None


def test_a_multi_year_event_is_deduplicated_via_events_latest(
    db_connection: Connection[Any],
) -> None:
    today = _today()
    now = datetime.now(tz=UTC)
    _add_event(
        db_connection,
        13,
        start_offset=-3,
        end_offset=2,
        source_year=today.year - 1,
        last_seen_at=now - timedelta(days=1),
    )
    _add_event(
        db_connection,
        13,
        start_offset=-3,
        end_offset=2,
        source_year=today.year,
        last_seen_at=now,
    )
    code = "TTEMSINGLES-----------FNL-000100----------"
    _add_match_entry(db_connection, 13, code)

    assert pending_matches(db_connection) == [((13, code), "live")]


def test_pending_matches_orders_live_then_most_recent_and_filters(
    db_connection: Connection[Any],
) -> None:
    _add_event(db_connection, 20, start_offset=-30, end_offset=-20)
    _add_event(db_connection, 21, start_offset=-3, end_offset=1)
    _add_event(db_connection, 22, start_offset=-60, end_offset=-50)
    code_a = "TTEMSINGLES-----------FNL-000100----------"
    code_b = "TTEMSINGLES-----------SFNL000100----------"
    _add_match_entry(db_connection, 20, code_a)
    _add_match_entry(db_connection, 21, code_a)
    _add_match_entry(db_connection, 22, code_a)
    _add_match_entry(db_connection, 22, code_b)

    assert pending_matches(db_connection) == [
        ((21, code_a), "live"),
        ((20, code_a), "new"),
        ((22, code_a), "new"),
        ((22, code_b), "new"),
    ]
    assert pending_matches(db_connection, limit=2) == [
        ((21, code_a), "live"),
        ((20, code_a), "new"),
    ]
    assert pending_matches(db_connection, limit=0) == []
    assert pending_matches(db_connection, event_ids=[22]) == [
        ((22, code_a), "new"),
        ((22, code_b), "new"),
    ]
    assert pending_matches(db_connection, limit=1, event_ids=[20, 22]) == [
        ((20, code_a), "new"),
    ]


def test_failure_backoff_cooldown_suppresses_and_recovers(
    db_connection: Connection[Any],
) -> None:
    now = datetime.now(tz=UTC)
    code_recent = "TTEMSINGLES-----------FNL-000100----------"
    code_expired = "TTEMSINGLES-----------SFNL000100----------"
    code_two = "TTEMSINGLES-----------QFNL000100----------"
    code_four = "TTEMSINGLES-----------R16-000100----------"
    code_recovered = "TTEMSINGLES-----------R32-000100----------"

    _add_event(db_connection, 30, start_offset=-30, end_offset=-20)
    for code in (code_recent, code_expired, code_two, code_four, code_recovered):
        _add_match_entry(db_connection, 30, code)

    # 1 recent failure (10 min ago -> 1h cooldown) suppresses.
    _add_run(
        db_connection,
        30,
        code_recent,
        status="failed",
        finished_at=now - timedelta(minutes=10),
    )

    # 1 expired failure (2 hours ago -> 1h cooldown elapsed) allows retry.
    _add_run(
        db_connection,
        30,
        code_expired,
        status="failed",
        finished_at=now - timedelta(hours=2),
    )

    # 2 recent failures (10 min and 2 hours ago -> consecutive=2, cooldown 4h).
    _add_run(
        db_connection,
        30,
        code_two,
        status="failed",
        finished_at=now - timedelta(hours=2),
    )
    _add_run(
        db_connection,
        30,
        code_two,
        status="failed",
        finished_at=now - timedelta(minutes=10),
    )

    # 4 recent failures (latest 2 days ago -> 7d cooldown).
    for days_ago in (5, 4, 3, 2):
        _add_run(
            db_connection,
            30,
            code_four,
            status="failed",
            finished_at=now - timedelta(days=days_ago),
        )

    # Newer succeeded run after failures clears cooldown immediately.
    _add_run(
        db_connection,
        30,
        code_recovered,
        status="failed",
        finished_at=now - timedelta(minutes=10),
    )
    _add_run(
        db_connection,
        30,
        code_recovered,
        status="succeeded",
        finished_at=now - timedelta(minutes=5),
    )

    assert _reason_for(db_connection, 30, code_recent) is None
    assert _reason_for(db_connection, 30, code_expired) == "new"
    assert _reason_for(db_connection, 30, code_two) is None
    assert _reason_for(db_connection, 30, code_four) is None
    assert _reason_for(db_connection, 30, code_recovered) == "new"

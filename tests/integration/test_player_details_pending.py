"""Integration tests: ``ingest_wtt.player_details_pending`` derives the work queue.

Every case writes its own match player sightings, payloads, and run history, then
reads the view or calls ``pending_players``. Runs against ``tt_stats_test``,
truncated around each test by the autouse fixture.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from psycopg import Connection
from psycopg.types.json import Jsonb

from tt_stats.core.ingest_runs import ENDPOINT_WTT_PLAYER_DETAILS
from tt_stats.players.collect import pending_players

pytestmark = pytest.mark.integration

_DIGEST = "1" * 64


def _add_seen_player(
    connection: Connection[Any],
    player_id: int,
    *,
    event_id: int = 100,
    document_code: str = "TTEMSINGLES-----------FNL-000100----------",
) -> None:
    """Insert a player sighting in ``match_details_players_unpacked``."""
    connection.execute(
        """
        INSERT INTO ingest_wtt.match_details_players_unpacked (
            event_id, document_code, player_id
        )
        VALUES (%s, %s, %s);
        """,
        (event_id, document_code, player_id),
    )


def _add_payload(
    connection: Connection[Any],
    player_id: int,
    *,
    fetched_at: datetime,
) -> None:
    """Insert a landed player_details payload."""
    connection.execute(
        """
        INSERT INTO ingest_wtt.player_details_raw (
            player_id, payload, http_status, received_count,
            payload_sha256, fetched_at
        )
        VALUES (%s, %s, 200, 1, %s, %s);
        """,
        (
            player_id,
            Jsonb({"Result": [{"IttfId": player_id}]}),
            _DIGEST,
            fetched_at,
        ),
    )


def _add_run(
    connection: Connection[Any],
    player_id: int,
    *,
    status: str,
    finished_at: datetime,
) -> None:
    """Insert an ingest_runs row for ``wtt_player_details``."""
    connection.execute(
        """
        INSERT INTO ingest_wtt.ingest_runs (
            endpoint, entity_key, status, started_at, finished_at, error
        )
        VALUES (%s, %s, %s, %s, %s, %s);
        """,
        (
            ENDPOINT_WTT_PLAYER_DETAILS,
            str(player_id),
            status,
            finished_at - timedelta(seconds=1),
            finished_at,
            "upstream error" if status == "failed" else None,
        ),
    )


def _reason_for(connection: Connection[Any], player_id: int) -> str | None:
    row = connection.execute(
        """
        SELECT reason
        FROM ingest_wtt.player_details_pending
        WHERE player_id = %s;
        """,
        (player_id,),
    ).fetchone()
    return None if row is None else str(row["reason"])


def test_an_unseen_player_is_queued_as_new(
    db_connection: Connection[Any],
) -> None:
    _add_seen_player(db_connection, 1001, event_id=100)
    _add_seen_player(db_connection, 1001, event_id=101)

    assert pending_players(db_connection) == [(1001, "new")]


def test_a_player_fetched_recently_is_not_queued(
    db_connection: Connection[Any],
) -> None:
    _add_seen_player(db_connection, 1002)
    _add_payload(
        db_connection,
        1002,
        fetched_at=datetime.now(tz=UTC) - timedelta(days=10),
    )

    assert _reason_for(db_connection, 1002) is None


def test_a_player_fetched_over_90_days_ago_is_queued_as_stale(
    db_connection: Connection[Any],
) -> None:
    _add_seen_player(db_connection, 1003)
    _add_payload(
        db_connection,
        1003,
        fetched_at=datetime.now(tz=UTC) - timedelta(days=91),
    )

    assert _reason_for(db_connection, 1003) == "stale"


def test_pending_players_orders_new_before_stale_and_applies_limit(
    db_connection: Connection[Any],
) -> None:
    now = datetime.now(tz=UTC)
    _add_seen_player(db_connection, 10)
    _add_payload(db_connection, 10, fetched_at=now - timedelta(days=95))

    _add_seen_player(db_connection, 20)
    _add_seen_player(db_connection, 30)
    _add_payload(db_connection, 30, fetched_at=now - timedelta(days=100))

    _add_seen_player(db_connection, 40)

    assert pending_players(db_connection) == [
        (20, "new"),
        (40, "new"),
        (10, "stale"),
        (30, "stale"),
    ]
    assert pending_players(db_connection, limit=3) == [
        (20, "new"),
        (40, "new"),
        (10, "stale"),
    ]
    assert pending_players(db_connection, limit=0) == []


def test_failure_backoff_cooldown_suppresses_and_recovers(
    db_connection: Connection[Any],
) -> None:
    now = datetime.now(tz=UTC)
    for player_id in (2001, 2002, 2003, 2004, 2005):
        _add_seen_player(db_connection, player_id)

    # 1 recent failure (10 min ago -> 1h cooldown) suppresses.
    _add_run(
        db_connection,
        2001,
        status="failed",
        finished_at=now - timedelta(minutes=10),
    )

    # 1 expired failure (2 hours ago -> 1h cooldown elapsed) allows retry.
    _add_run(
        db_connection,
        2002,
        status="failed",
        finished_at=now - timedelta(hours=2),
    )

    # 2 recent failures (10 min and 2 hours ago -> consecutive=2, cooldown 4h).
    _add_run(
        db_connection,
        2003,
        status="failed",
        finished_at=now - timedelta(hours=2),
    )
    _add_run(
        db_connection,
        2003,
        status="failed",
        finished_at=now - timedelta(minutes=10),
    )

    # 4 recent failures (latest 2 days ago -> 7d cooldown).
    for days_ago in (5, 4, 3, 2):
        _add_run(
            db_connection,
            2004,
            status="failed",
            finished_at=now - timedelta(days=days_ago),
        )

    # Newer succeeded run clears cooldown immediately.
    _add_run(
        db_connection,
        2005,
        status="failed",
        finished_at=now - timedelta(minutes=10),
    )
    _add_run(
        db_connection,
        2005,
        status="succeeded",
        finished_at=now - timedelta(minutes=5),
    )

    assert _reason_for(db_connection, 2001) is None
    assert _reason_for(db_connection, 2002) == "new"
    assert _reason_for(db_connection, 2003) is None
    assert _reason_for(db_connection, 2004) is None
    assert _reason_for(db_connection, 2005) == "new"

"""Integration tests: a landed sponsor payload unpacks into one row per sponsor.

Runs against ``tt_stats_test``, truncated around every test by the autouse
fixture, so nothing here needs cleanup.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Any

import pytest
from psycopg import Connection, errors

from tt_stats.event_sponsors.collect.event_sponsors import EventSponsorsSummary
from tt_stats.event_sponsors.land import land_event_sponsors
from tt_stats.event_sponsors.land.unpack import unpack_events

pytestmark = pytest.mark.integration

EVENT_ID = 9_200_323
OTHER_EVENT_ID = 9_200_324

Payload = list[dict[str, Any]] | dict[str, Any]


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "eventId": EVENT_ID,
        "eventSponsorId": 5845,
        "sponsorTypeId": 15,
        "logo": "org_logos/example.png",
        "darkLogo": None,
        "sponsorLink": "https://example.com/",
        "sponsorTypeName": "Global Premier Partner",
    }
    row.update(overrides)
    return row


def _summary(event_id: int, payload: Payload) -> EventSponsorsSummary:
    body = json.dumps(payload).encode("utf-8")
    return EventSponsorsSummary(
        event_id=event_id,
        status_code=200,
        received_count=len(payload) if isinstance(payload, list) else 0,
        payload_sha256=hashlib.sha256(body).hexdigest(),
        fetched_at=datetime.now(tz=UTC),
        error=None,
        reason="new",
    )


def _land(connection: Connection[Any], event_id: int, payload: Payload) -> None:
    land_event_sponsors(connection, _summary(event_id, payload), payload)


def _unpacked(
    connection: Connection[Any],
    event_id: int = EVENT_ID,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            event_id,
            event_sponsor_id,
            sponsor_type_id,
            sponsor_type_name,
            logo,
            dark_logo,
            sponsor_link,
            first_seen_at,
            last_seen_at
        FROM ingest.event_sponsors_unpacked
        WHERE event_id = %s
        ORDER BY event_sponsor_id;
        """,
        (event_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def test_landing_unpacks_one_row_per_sponsor(
    db_connection: Connection[Any],
) -> None:
    _land(
        db_connection,
        EVENT_ID,
        [
            _row(eventSponsorId=5845, sponsorTypeName="Global Premier Partner"),
            _row(
                eventSponsorId=3251,
                sponsorTypeId=4,
                sponsorTypeName="Official Ball",
            ),
        ],
    )

    rows = _unpacked(db_connection)
    assert [row["event_sponsor_id"] for row in rows] == [3251, 5845]
    assert rows[0]["sponsor_type_name"] == "Official Ball"
    assert rows[1]["sponsor_type_name"] == "Global Premier Partner"


def test_missing_optional_fields_unpack_as_null(
    db_connection: Connection[Any],
) -> None:
    """Missing descriptive keys (not just null values) must unpack cleanly."""
    minimal = {"eventId": EVENT_ID, "eventSponsorId": 9001}
    _land(db_connection, EVENT_ID, [minimal])

    rows = _unpacked(db_connection)
    assert len(rows) == 1
    row = rows[0]
    assert row["event_sponsor_id"] == 9001
    assert row["sponsor_type_id"] is None
    assert row["sponsor_type_name"] is None
    assert row["logo"] is None
    assert row["dark_logo"] is None
    assert row["sponsor_link"] is None


def test_empty_payload_unpacks_to_zero_rows(
    db_connection: Connection[Any],
) -> None:
    _land(db_connection, EVENT_ID, [])

    assert _unpacked(db_connection) == []


def test_missing_sponsor_id_rolls_back_the_whole_event(
    db_connection: Connection[Any],
) -> None:
    bad = _row()
    bad.pop("eventSponsorId")

    with pytest.raises((errors.NotNullViolation, RuntimeError)):
        _land(db_connection, EVENT_ID, [bad])

    raw = db_connection.execute(
        "SELECT count(*) AS n FROM ingest.event_sponsors_raw WHERE event_id = %s;",
        (EVENT_ID,),
    ).fetchone()
    assert raw == {"n": 0}
    assert _unpacked(db_connection) == []


def test_re_unpacking_is_idempotent_and_advances_last_seen_at(
    db_connection: Connection[Any],
) -> None:
    _land(db_connection, EVENT_ID, [_row(eventSponsorId=5845)])
    first = _unpacked(db_connection)[0]

    time.sleep(0.01)
    _land(
        db_connection,
        EVENT_ID,
        [_row(eventSponsorId=5845, sponsorTypeName="Updated Partner")],
    )
    second = _unpacked(db_connection)[0]

    assert second["first_seen_at"] == first["first_seen_at"]
    assert second["last_seen_at"] > first["last_seen_at"]
    assert second["sponsor_type_name"] == "Updated Partner"


def test_dry_run_rolls_back_without_writing(
    db_connection: Connection[Any],
    test_settings: Any,
) -> None:
    _land(db_connection, OTHER_EVENT_ID, [_row(eventId=OTHER_EVENT_ID)])
    db_connection.execute("DELETE FROM ingest.event_sponsors_unpacked;")

    results = unpack_events([OTHER_EVENT_ID], dry_run=True)

    assert results == [(OTHER_EVENT_ID, 1)]
    assert _unpacked(db_connection, OTHER_EVENT_ID) == []

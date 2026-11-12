"""Integration tests: landing an event's sponsor payload is one row, one transaction.

Runs against ``tt_stats_test``, which the autouse fixture truncates around every
test, so no cleanup is needed here.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from psycopg import Connection

from tt_stats.event_sponsors.collect.event_sponsors import EventSponsorsSummary
from tt_stats.event_sponsors.land import land_event_sponsors

pytestmark = pytest.mark.integration

# Private id range, following the convention the events/matches fixtures use.
EVENT_ID = 9_200_323


def _sponsor(sponsor_id: int) -> dict[str, Any]:
    return {
        "eventId": EVENT_ID,
        "eventSponsorId": sponsor_id,
        "sponsorTypeId": 15,
        "logo": "org_logos/example.png",
        "darkLogo": None,
        "sponsorLink": "https://example.com/",
        "sponsorTypeName": "Global Premier Partner",
    }


def _summary(payload: list[dict[str, Any]]) -> EventSponsorsSummary:
    body = json.dumps(payload).encode("utf-8")
    return EventSponsorsSummary(
        event_id=EVENT_ID,
        status_code=200,
        received_count=len(payload),
        payload_sha256=hashlib.sha256(body).hexdigest(),
        fetched_at=datetime.now(tz=UTC),
        error=None,
        reason="new",
    )


def test_landing_upserts_one_row_and_is_idempotent(
    db_connection: Connection[Any],
) -> None:
    payload = [_sponsor(5845), _sponsor(3251)]
    summary = _summary(payload)

    land_event_sponsors(db_connection, summary, payload)
    land_event_sponsors(db_connection, summary, payload)

    rows = db_connection.execute(
        """
        SELECT event_id, payload, http_status, received_count, payload_sha256, source
        FROM ingest.event_sponsors_raw
        WHERE event_id = %s;
        """,
        (EVENT_ID,),
    ).fetchall()

    assert len(rows) == 1
    row = rows[0]
    assert row["payload"] == payload
    assert row["http_status"] == 200
    assert row["received_count"] == 2
    assert row["payload_sha256"] == summary.payload_sha256
    assert row["source"] == "wtt_geteventequipmentwithlogo"

    unpacked = db_connection.execute(
        "SELECT count(*) AS n FROM ingest.event_sponsors_unpacked WHERE event_id = %s;",
        (EVENT_ID,),
    ).fetchone()
    assert unpacked == {"n": 2}


def test_an_empty_sponsor_list_is_recorded_as_an_answer(
    db_connection: Connection[Any],
) -> None:
    """``[]`` must land so the pending queue knows the event was already asked."""
    payload: list[dict[str, Any]] = []
    summary = _summary(payload)

    land_event_sponsors(db_connection, summary, payload)

    row = db_connection.execute(
        """
        SELECT payload, received_count
        FROM ingest.event_sponsors_raw
        WHERE event_id = %s;
        """,
        (EVENT_ID,),
    ).fetchone()

    assert row is not None
    assert row["payload"] == []
    assert row["received_count"] == 0


def test_landing_refuses_a_failed_summary(
    db_connection: Connection[Any],
) -> None:
    """A failed fetch must never overwrite a good payload."""
    payload = [_sponsor(5845)]
    good = _summary(payload)
    land_event_sponsors(db_connection, good, payload)

    failed = EventSponsorsSummary(
        event_id=EVENT_ID,
        status_code=500,
        received_count=0,
        payload_sha256=None,
        fetched_at=datetime.now(tz=UTC),
        error="WTT request failed",
        reason="new",
    )

    with pytest.raises(ValueError, match="cannot land a failed event"):
        land_event_sponsors(db_connection, failed, [])

    row = db_connection.execute(
        """
        SELECT received_count
        FROM ingest.event_sponsors_raw
        WHERE event_id = %s;
        """,
        (EVENT_ID,),
    ).fetchone()
    assert row == {"received_count": 1}


def test_landing_rolls_back_when_summary_count_disagrees_with_payload(
    db_connection: Connection[Any],
) -> None:
    """A summary whose received_count disagrees with the stored JSON must abort."""
    payload = [_sponsor(5845), _sponsor(3251)]
    body = json.dumps(payload).encode("utf-8")
    mismatched = EventSponsorsSummary(
        event_id=EVENT_ID,
        status_code=200,
        received_count=1,
        payload_sha256=hashlib.sha256(body).hexdigest(),
        fetched_at=datetime.now(tz=UTC),
        error=None,
        reason="new",
    )

    with pytest.raises(RuntimeError, match="Unpack validation failed"):
        land_event_sponsors(db_connection, mismatched, payload)

    raw = db_connection.execute(
        "SELECT count(*) AS n FROM ingest.event_sponsors_raw WHERE event_id = %s;",
        (EVENT_ID,),
    ).fetchone()
    unpacked = db_connection.execute(
        "SELECT count(*) AS n FROM ingest.event_sponsors_unpacked WHERE event_id = %s;",
        (EVENT_ID,),
    ).fetchone()
    assert raw == {"n": 0}
    assert unpacked == {"n": 0}

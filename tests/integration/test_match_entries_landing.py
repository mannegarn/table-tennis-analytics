"""Integration tests: landing a match list is one row, one transaction.

Runs against ``tt_stats_test``, which the autouse fixture truncates around every
test, so no cleanup is needed here.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from psycopg import Connection

from tt_stats.matches.collect.match_entries import MatchEntriesSummary
from tt_stats.matches.land import land_match_entries

pytestmark = pytest.mark.integration

# Private id range, following the convention the events fixtures use.
EVENT_ID = 9_100_398


def _match(match_id: int) -> dict[str, Any]:
    return {
        "id": match_id,
        "eventId": str(EVENT_ID),
        "documentCode": "TTEMSINGLES-----------R64-002500----------",
        "subEventType": "Men Singles",
        "fullResults": "OFFICIAL",
        "startDateLocal": "2025-09-29T18:35:00",
        "match_card": None,
        "extended_info": None,
        "messagePayload": None,
    }


def _summary(payload: list[dict[str, Any]]) -> MatchEntriesSummary:
    body = json.dumps(payload).encode("utf-8")
    return MatchEntriesSummary(
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
    payload = [_match(1), _match(2)]
    summary = _summary(payload)

    land_match_entries(summary, payload)
    land_match_entries(summary, payload)

    rows = db_connection.execute(
        """
        SELECT event_id, payload, http_status, received_count, payload_sha256, source
        FROM ingest.match_entries_payloads_raw
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
    assert row["source"] == "wtt_getofficialresult"


def test_an_empty_match_list_is_recorded_as_an_answer(
    db_connection: Connection[Any],
) -> None:
    """``[]`` must land: it is how "WTT has nothing for this event" is stored."""
    payload: list[dict[str, Any]] = []
    summary = _summary(payload)

    land_match_entries(summary, payload)

    row = db_connection.execute(
        """
        SELECT payload, received_count
        FROM ingest.match_entries_payloads_raw
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
    payload = [_match(1)]
    good = _summary(payload)
    land_match_entries(good, payload)

    failed = MatchEntriesSummary(
        event_id=EVENT_ID,
        status_code=500,
        received_count=0,
        payload_sha256=None,
        fetched_at=datetime.now(tz=UTC),
        error="WTT request failed",
        reason="new",
    )

    with pytest.raises(ValueError, match="cannot land a failed event"):
        land_match_entries(failed, [])

    row = db_connection.execute(
        """
        SELECT received_count
        FROM ingest.match_entries_payloads_raw
        WHERE event_id = %s;
        """,
        (EVENT_ID,),
    ).fetchone()
    assert row == {"received_count": 1}

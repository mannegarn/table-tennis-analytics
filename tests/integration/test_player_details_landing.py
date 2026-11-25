"""Integration tests: landing, unpacking, and pending view for player_details."""

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from psycopg import Connection

from tt_stats.core.collector import EMPTY_SHA256, FetchSummary
from tt_stats.players.land import land_player_details_chunk

pytestmark = pytest.mark.integration

PLAYER_1 = 9_121_558
PLAYER_2 = 9_121_559


def _full_payload(player_id: int) -> dict[str, Any]:
    return {
        "Version": "0.1.0",
        "Source": "ITTF/WTT",
        "System": "Table tennis Sports Data API",
        "StatusCode": 200,
        "RequestId": "test-req-1",
        "ResponseDate": "11/12/2026",
        "ResponseTime": "22:53:44",
        "Result": [
            {
                "IttfId": str(player_id),
                "PlayerName": "TEST Player",
                "PlayerGivenName": "Player",
                "PlayerFamilyName": "TEST",
                "PlayerFamilyNameFirst": "TEST Player",
                "CountryCode": "CHN",
                "CountryName": "China",
                "NationalityCode": "CHN",
                "NationalityName": "China",
                "OrganizationCode": "CHN",
                "OrganizationName": "CHINA",
                "Gender": "M",
                "Age": "31",
                "DOB": "01/15/1995 00:00:00",
                "Handedness": "Left Hand",
                "Grip": "Shakehand",
                "Style": None,
                "EquipmentSponsor": None,
                "BladeType": None,
                "RacketColoringA": None,
                "RacketColoringB": None,
                "RacketCoveringA": None,
                "RacketCoveringB": None,
                "ActiveSince": None,
                "EarningsCareer": None,
                "EarningsYTD": None,
                "LastMatch": None,
                "NextMatch": None,
                "HeadShot": "https://wttsimfiles.blob.core.windows.net/wtt-media/photos/400px/121558.png",
                "Bio": None,
                "HeadshotR": "https://wttsimfiles.blob.core.windows.net/wtt-media/photos/1080px/R.png",
                "HeadshotL": "https://wttsimfiles.blob.core.windows.net/wtt-media/photos/1080px/L.png",
            }
        ],
    }


def _summary(
    player_id: int,
    payload: dict[str, Any],
    *,
    status_code: int = 200,
    received_count: int = 1,
) -> FetchSummary[int]:
    body = json.dumps(payload).encode("utf-8")
    return FetchSummary(
        key=player_id,
        status_code=status_code,
        received_count=received_count,
        payload_sha256=hashlib.sha256(body).hexdigest(),
        fetched_at=datetime.now(tz=UTC),
        error=None,
        reason="new",
    )


def test_land_chunk_upserts_raw_player_details(
    db_connection: Connection[Any],
) -> None:
    payload_1 = _full_payload(PLAYER_1)
    payload_empty: dict[str, Any] = {"Result": []}
    items = [
        (_summary(PLAYER_1, payload_1), payload_1),
        (
            _summary(PLAYER_2, payload_empty, status_code=404, received_count=0),
            payload_empty,
        ),
    ]

    land_player_details_chunk(db_connection, items)
    land_player_details_chunk(db_connection, items)

    raw_rows = db_connection.execute(
        """
        SELECT
            player_id,
            http_status,
            received_count,
            source,
            payload -> 'Result' -> 0 ->> 'PlayerName' AS player_name
        FROM ingest_wtt.player_details_raw
        ORDER BY player_id;
        """
    ).fetchall()
    assert raw_rows == [
        {
            "player_id": PLAYER_1,
            "http_status": 200,
            "received_count": 1,
            "source": "wtt_player_details",
            "player_name": "TEST Player",
        },
        {
            "player_id": PLAYER_2,
            "http_status": 404,
            "received_count": 0,
            "source": "wtt_player_details",
            "player_name": None,
        },
    ]


def test_player_details_pending_surfaces_unseen_match_players(
    db_connection: Connection[Any],
) -> None:
    db_connection.execute(
        """
        INSERT INTO ingest_wtt.match_details_players_unpacked (
            event_id,
            document_code,
            player_id
        )
        VALUES
            (9991, 'DOC-1', %s),
            (9992, 'DOC-2', %s),
            (9991, 'DOC-1', %s);
        """,
        (PLAYER_1, PLAYER_1, PLAYER_2),
    )

    pending = db_connection.execute(
        """
        SELECT player_id, reason
        FROM ingest_wtt.player_details_pending
        ORDER BY player_id;
        """
    ).fetchall()
    assert pending == [
        {"player_id": PLAYER_1, "reason": "new"},
        {"player_id": PLAYER_2, "reason": "new"},
    ]

    payload_1 = _full_payload(PLAYER_1)
    land_player_details_chunk(
        db_connection, [(_summary(PLAYER_1, payload_1), payload_1)]
    )

    remaining = db_connection.execute(
        """
        SELECT player_id, reason
        FROM ingest_wtt.player_details_pending
        ORDER BY player_id;
        """
    ).fetchall()
    assert remaining == [{"player_id": PLAYER_2, "reason": "new"}]


def test_not_found_never_overwrites_non_empty_player_profile(
    db_connection: Connection[Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """404 placeholder lands on new/empty rows, never overwrites a 200 profile."""
    t1 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    t2 = t1 + timedelta(days=1)

    empty_payload: dict[str, Any] = {"Result": []}
    s_empty_new = FetchSummary(
        key=PLAYER_2,
        status_code=404,
        received_count=0,
        payload_sha256=EMPTY_SHA256,
        fetched_at=t1,
        error=None,
        reason="new",
    )
    land_player_details_chunk(db_connection, [(s_empty_new, empty_payload)])

    # empty -> non-empty updates PLAYER_2; seed PLAYER_1 at t1
    p1 = _full_payload(PLAYER_1)
    p2 = _full_payload(PLAYER_2)
    s1 = FetchSummary(
        key=PLAYER_1,
        status_code=200,
        received_count=1,
        payload_sha256=hashlib.sha256(json.dumps(p1).encode()).hexdigest(),
        fetched_at=t1,
        error=None,
        reason="new",
    )
    s2 = FetchSummary(
        key=PLAYER_2,
        status_code=200,
        received_count=1,
        payload_sha256=hashlib.sha256(json.dumps(p2).encode()).hexdigest(),
        fetched_at=t2,
        error=None,
        reason="new",
    )
    land_player_details_chunk(db_connection, [(s1, p1), (s2, p2)])

    # non-empty -> 404 on PLAYER_1 is blocked while preserving t1 and 200 payload
    s1_404 = FetchSummary(
        key=PLAYER_1,
        status_code=404,
        received_count=0,
        payload_sha256=EMPTY_SHA256,
        fetched_at=t2,
        error=None,
        reason="stale",
    )
    with caplog.at_level(logging.WARNING):
        land_player_details_chunk(db_connection, [(s1_404, empty_payload)])

    row1 = db_connection.execute(
        "SELECT payload, received_count, http_status, fetched_at "
        "FROM ingest_wtt.player_details_raw WHERE player_id = %s;",
        (PLAYER_1,),
    ).fetchone()
    row2 = db_connection.execute(
        "SELECT received_count, http_status FROM ingest_wtt.player_details_raw "
        "WHERE player_id = %s;",
        (PLAYER_2,),
    ).fetchone()
    assert row1 == {
        "payload": p1,
        "received_count": 1,
        "http_status": 200,
        "fetched_at": t1,
    }
    assert row2 == {"received_count": 1, "http_status": 200}
    assert any(
        r.getMessage() == "player_details_empty_overwrite_blocked"
        for r in caplog.records
    )

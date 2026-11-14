"""Integration tests: landing, unpacking, and pending view for player_details."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from psycopg import Connection

from tt_stats.players.collect.player_details import PlayerDetailsSummary
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
                "PlayerName": "WANG Chuqin",
                "PlayerGivenName": "Chuqin",
                "PlayerFamilyName": "WANG",
                "PlayerFamilyNameFirst": "WANG Chuqin",
                "CountryCode": "CHN",
                "CountryName": "China",
                "NationalityCode": "CHN",
                "NationalityName": "China",
                "OrganizationCode": "CHN",
                "OrganizationName": "CHINA",
                "Gender": "M",
                "Age": "26",
                "DOB": "05/11/2000 00:00:00",
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
) -> PlayerDetailsSummary:
    body = json.dumps(payload).encode("utf-8")
    return PlayerDetailsSummary(
        player_id=player_id,
        status_code=status_code,
        received_count=received_count,
        payload_sha256=hashlib.sha256(body).hexdigest(),
        fetched_at=datetime.now(tz=UTC),
        error=None,
        reason="new",
    )


def test_land_chunk_upserts_raw_and_unpacks_flat_player_row(
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
        SELECT player_id, http_status, received_count, source
        FROM ingest.player_details_raw
        ORDER BY player_id;
        """
    ).fetchall()
    assert raw_rows == [
        {
            "player_id": PLAYER_1,
            "http_status": 200,
            "received_count": 1,
            "source": "wtt_player_details",
        },
        {
            "player_id": PLAYER_2,
            "http_status": 404,
            "received_count": 0,
            "source": "wtt_player_details",
        },
    ]

    unpacked = db_connection.execute(
        """
        SELECT
            player_id,
            player_name,
            country_code,
            gender,
            age,
            handedness,
            grip
        FROM ingest.player_details_unpacked;
        """
    ).fetchall()
    assert unpacked == [
        {
            "player_id": PLAYER_1,
            "player_name": "WANG Chuqin",
            "country_code": "CHN",
            "gender": "M",
            "age": 26,
            "handedness": "Left Hand",
            "grip": "Shakehand",
        }
    ]


def test_player_details_pending_surfaces_unseen_match_players(
    db_connection: Connection[Any],
) -> None:
    db_connection.execute(
        """
        INSERT INTO ingest.match_details_players_unpacked (
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
        FROM ingest.player_details_pending
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
        FROM ingest.player_details_pending
        ORDER BY player_id;
        """
    ).fetchall()
    assert remaining == [{"player_id": PLAYER_2, "reason": "new"}]

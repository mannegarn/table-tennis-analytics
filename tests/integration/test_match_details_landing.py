"""Integration tests: chunked landing and minimal player unpack for match_details."""

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from psycopg import Connection

from tt_stats.core.collector import EMPTY_SHA256, FetchSummary
from tt_stats.matches.land import land_match_details_chunk

pytestmark = pytest.mark.integration

EVENT_ID = 9_300_325
DOC_1 = "TTEMSINGLES-----------R64-000100----------"
DOC_2 = "TTEDDOUBLES-----------FNL-000100----------"


def _payload(document_code: str, player_groups: list[list[str]]) -> dict[str, Any]:
    return {
        "eventId": str(EVENT_ID),
        "documentCode": document_code,
        "competitiors": [
            {"players": [{"playerId": pid} for pid in group]} for group in player_groups
        ],
    }


def _summary(
    document_code: str, payload: dict[str, Any]
) -> FetchSummary[tuple[int, str]]:
    body = json.dumps(payload).encode("utf-8")
    return FetchSummary(
        key=(EVENT_ID, document_code),
        status_code=200,
        received_count=2,
        payload_sha256=hashlib.sha256(body).hexdigest(),
        fetched_at=datetime.now(tz=UTC),
        error=None,
        reason="new",
    )


def test_land_chunk_upserts_raw_and_unpacks_distinct_players(
    db_connection: Connection[Any],
) -> None:
    p1 = _payload(DOC_1, [["101"], ["102"]])
    p2 = _payload(DOC_2, [["201", "202"], ["203", "204"]])
    items = [(_summary(DOC_1, p1), p1), (_summary(DOC_2, p2), p2)]

    land_match_details_chunk(db_connection, items)
    land_match_details_chunk(db_connection, items)

    raw_rows = db_connection.execute(
        """
        SELECT document_code, source
        FROM ingest_wtt.match_details_raw
        WHERE event_id = %s
        ORDER BY document_code;
        """,
        (EVENT_ID,),
    ).fetchall()
    assert [(r["document_code"], r["source"]) for r in raw_rows] == [
        (DOC_2, "wtt_matchcentre"),
        (DOC_1, "wtt_matchcentre"),
    ]

    players = db_connection.execute(
        """
        SELECT document_code, player_id
        FROM ingest_wtt.match_details_players_unpacked
        WHERE event_id = %s
        ORDER BY document_code, player_id;
        """,
        (EVENT_ID,),
    ).fetchall()
    assert [(r["document_code"], r["player_id"]) for r in players] == [
        (DOC_2, 201),
        (DOC_2, 202),
        (DOC_2, 203),
        (DOC_2, 204),
        (DOC_1, 101),
        (DOC_1, 102),
    ]


def test_land_chunk_unpacks_nested_team_rubber_players(
    db_connection: Connection[Any],
) -> None:
    doc_camel = "TTEMTEAM--------------QFNL00010000--------"
    doc_pascal = "TTEMTEAM-----------U15QFNL00010000--------"

    camel_payload: dict[str, Any] = {
        "eventId": str(EVENT_ID),
        "documentCode": doc_camel,
        "competitiors": [{"players": [{"playerId": "301"}]}],
        "teamParentData": {
            "extended_info": {
                "matches": [
                    {
                        "value": "TTEMTEAM--------------QFNL00010001--------",
                        "match_result": {
                            "competitiors": [
                                {"players": [{"playerId": "301"}]},
                                {"players": [{"playerId": "302"}]},
                            ]
                        },
                    },
                    {
                        "value": "TTEMTEAM--------------QFNL00010002--------",
                        "match_result": {
                            "competitiors": [
                                {"players": [{"playerId": "303"}]},
                                {"players": [{"playerId": ""}]},
                            ]
                        },
                    },
                    {
                        "value": "TTEMTEAM--------------QFNL00010003--------",
                    },
                ]
            }
        },
    }

    pascal_payload: dict[str, Any] = {
        "eventId": str(EVENT_ID),
        "documentCode": doc_pascal,
        "teamParentData": {
            "extended_info": {
                "matches": [
                    {
                        "Value": "TTEMTEAM-----------U15QFNL00010001--------",
                        "match_result": {
                            "Competitiors": [
                                {"Players": [{"PlayerId": "401"}]},
                                {"Players": [{"PlayerId": "402"}]},
                            ]
                        },
                    }
                ]
            }
        },
    }

    items = [
        (_summary(doc_camel, camel_payload), camel_payload),
        (_summary(doc_pascal, pascal_payload), pascal_payload),
    ]
    land_match_details_chunk(db_connection, items)
    land_match_details_chunk(db_connection, items)

    players = db_connection.execute(
        """
        SELECT document_code, player_id
        FROM ingest_wtt.match_details_players_unpacked
        WHERE event_id = %s
        ORDER BY document_code, player_id;
        """,
        (EVENT_ID,),
    ).fetchall()
    assert [(r["document_code"], r["player_id"]) for r in players] == [
        (doc_camel, 301),
        (doc_camel, 302),
        (doc_camel, 303),
        (doc_pascal, 401),
        (doc_pascal, 402),
    ]


def test_not_found_and_empty_transitions_and_overwrite_guard(
    db_connection: Connection[Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """404/empty lands on new or empty rows, but never overwrites non-empty data."""
    t1 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    t2 = t1 + timedelta(days=1)
    t3 = t2 + timedelta(days=1)
    t4 = t3 + timedelta(days=1)

    def not_found_summary(
        doc: str, fetched_at: datetime
    ) -> FetchSummary[tuple[int, str]]:
        return FetchSummary(
            key=(EVENT_ID, doc),
            status_code=404,
            received_count=0,
            payload_sha256=EMPTY_SHA256,
            fetched_at=fetched_at,
            error=None,
            reason="stale",
        )

    # 1. new -> 404 {} lands, then 404 {} -> 404 {} advances fetched_at,
    #    then 404 {} -> 200 non-empty replaces {} and unpacks players.
    land_match_details_chunk(db_connection, [(not_found_summary(DOC_2, t1), {})])
    land_match_details_chunk(db_connection, [(not_found_summary(DOC_2, t2), {})])
    row = db_connection.execute(
        "SELECT payload, http_status, fetched_at FROM ingest_wtt.match_details_raw "
        "WHERE event_id = %s AND document_code = %s;",
        (EVENT_ID, DOC_2),
    ).fetchone()
    assert row == {"payload": {}, "http_status": 404, "fetched_at": t2}

    p2 = _payload(DOC_2, [["201"], ["202"]])
    s2 = FetchSummary(
        key=(EVENT_ID, DOC_2),
        status_code=200,
        received_count=2,
        payload_sha256=hashlib.sha256(json.dumps(p2).encode()).hexdigest(),
        fetched_at=t3,
        error=None,
        reason="live",
    )
    land_match_details_chunk(db_connection, [(s2, p2)])

    # 2. Seed DOC_1 with non-empty 200 at t1, then send a mixed chunk:
    #    - DOC_1 gets a 404 {} at t4 (must be blocked, preserving t1 and players)
    #    - DOC_3 is brand-new 200 at t4 (must still land and unpack)
    doc_3 = "TTEMSINGLES-----------R32-000100----------"
    p1 = _payload(DOC_1, [["101"], ["102"]])
    s1 = FetchSummary(
        key=(EVENT_ID, DOC_1),
        status_code=200,
        received_count=2,
        payload_sha256=hashlib.sha256(json.dumps(p1).encode()).hexdigest(),
        fetched_at=t1,
        error=None,
        reason="live",
    )
    land_match_details_chunk(db_connection, [(s1, p1)])

    p3 = _payload(doc_3, [["301"], ["302"]])
    s3 = FetchSummary(
        key=(EVENT_ID, doc_3),
        status_code=200,
        received_count=2,
        payload_sha256=hashlib.sha256(json.dumps(p3).encode()).hexdigest(),
        fetched_at=t4,
        error=None,
        reason="new",
    )
    with caplog.at_level(logging.WARNING):
        land_match_details_chunk(
            db_connection,
            [(not_found_summary(DOC_1, t4), {}), (s3, p3)],
        )

    preserved = db_connection.execute(
        "SELECT payload, http_status, fetched_at FROM ingest_wtt.match_details_raw "
        "WHERE event_id = %s AND document_code = %s;",
        (EVENT_ID, DOC_1),
    ).fetchone()
    assert preserved == {"payload": p1, "http_status": 200, "fetched_at": t1}

    doc3_row = db_connection.execute(
        "SELECT payload, http_status FROM ingest_wtt.match_details_raw "
        "WHERE event_id = %s AND document_code = %s;",
        (EVENT_ID, doc_3),
    ).fetchone()
    assert doc3_row == {"payload": p3, "http_status": 200}

    players = db_connection.execute(
        "SELECT document_code, player_id "
        "FROM ingest_wtt.match_details_players_unpacked "
        "WHERE event_id = %s AND document_code IN (%s, %s) "
        "ORDER BY document_code, player_id;",
        (EVENT_ID, DOC_1, doc_3),
    ).fetchall()
    assert [(r["document_code"], r["player_id"]) for r in players] == [
        (doc_3, 301),
        (doc_3, 302),
        (DOC_1, 101),
        (DOC_1, 102),
    ]
    assert any(
        r.getMessage() == "match_details_empty_overwrite_blocked"
        for r in caplog.records
    )

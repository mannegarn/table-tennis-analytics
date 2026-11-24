"""Integration tests: chunked landing and minimal player unpack for match_details."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from psycopg import Connection

from tt_stats.core.collector import FetchSummary
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

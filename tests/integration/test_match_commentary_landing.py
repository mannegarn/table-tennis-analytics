"""Integration tests: chunked landing and unpacking for match_commentary."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from psycopg import Connection

from tt_stats.matches.collect.match_commentary import MatchCommentarySummary
from tt_stats.matches.land import land_match_commentary_chunk

pytestmark = pytest.mark.integration

EVENT_ID = 9_400_325
DOC_1 = "TTEWSINGLES-----------RND1002700----------"


def _line(seq: int, action: str, text: str) -> dict[str, Any]:
    return {
        "id": f"uuid-{seq}",
        "event_doc_code": f"{EVENT_ID}_{DOC_1}",
        "data": {
            "ACTIONTYPE": action,
            "UTCDATE": "2026-11-10T00:00:00Z",
            "UTCTIME": "152650049",
            "LOCALDATE": "2026-11-10T00:00:00",
            "LOCALTIME": "182650049",
            "EVENTID": str(EVENT_ID),
            "MATCHID": DOC_1,
            "SEQUENCENUMBER": str(seq),
            "COMMENTARYTEXT": text,
        },
        "_ts": 1794324411 + seq,
    }


def _summary(payload: list[dict[str, Any]]) -> MatchCommentarySummary:
    body = json.dumps(payload).encode("utf-8")
    return MatchCommentarySummary(
        event_id=EVENT_ID,
        document_code=DOC_1,
        status_code=200,
        received_count=len(payload),
        payload_sha256=hashlib.sha256(body).hexdigest(),
        fetched_at=datetime.now(tz=UTC),
        error=None,
        reason="new",
    )


def test_land_chunk_upserts_raw_and_unpacks_commentary_lines(
    db_connection: Connection[Any],
) -> None:
    payload = [
        _line(1, "SL", "Start of Match"),
        _line(3, "SSR", "Service change: YOVKOVA Maria to serve"),
        _line(7, "PT", "Point won by ERKEBAEVA Asel, Game 1, Game Score 0-1"),
    ]
    items = [(_summary(payload), payload)]

    land_match_commentary_chunk(db_connection, items)
    land_match_commentary_chunk(db_connection, items)

    raw_row = db_connection.execute(
        """
        SELECT received_count, source
        FROM ingest.match_commentary_raw
        WHERE event_id = %s AND document_code = %s;
        """,
        (EVENT_ID, DOC_1),
    ).fetchone()
    assert raw_row == {"received_count": 3, "source": "wtt_match_commentary"}

    unpacked = db_connection.execute(
        """
        SELECT sequence_number, action_type, commentary_text
        FROM ingest.match_commentary_unpacked
        WHERE event_id = %s AND document_code = %s
        ORDER BY sequence_number;
        """,
        (EVENT_ID, DOC_1),
    ).fetchall()
    assert [
        (r["sequence_number"], r["action_type"], r["commentary_text"]) for r in unpacked
    ] == [
        (1, "SL", "Start of Match"),
        (3, "SSR", "Service change: YOVKOVA Maria to serve"),
        (7, "PT", "Point won by ERKEBAEVA Asel, Game 1, Game Score 0-1"),
    ]

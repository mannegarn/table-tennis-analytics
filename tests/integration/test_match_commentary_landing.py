"""Integration tests: chunked raw landing for match_commentary."""

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from psycopg import Connection

from tt_stats.core.collector import EMPTY_SHA256, FetchSummary
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


def _summary(payload: list[dict[str, Any]]) -> FetchSummary[tuple[int, str]]:
    body = json.dumps(payload).encode("utf-8")
    return FetchSummary(
        key=(EVENT_ID, DOC_1),
        status_code=200,
        received_count=len(payload),
        payload_sha256=hashlib.sha256(body).hexdigest(),
        fetched_at=datetime.now(tz=UTC),
        error=None,
        reason="new",
    )


def test_land_chunk_upserts_raw_commentary_payload(
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
        SELECT received_count, source, jsonb_array_length(payload) AS item_count
        FROM ingest_wtt.match_commentary_raw
        WHERE event_id = %s AND document_code = %s;
        """,
        (EVENT_ID, DOC_1),
    ).fetchone()
    assert raw_row == {
        "received_count": 3,
        "source": "wtt_match_commentary",
        "item_count": 3,
    }


def test_not_found_and_empty_transitions_and_overwrite_guard(
    db_connection: Connection[Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """404/empty lands on new/empty rows, but never overwrites non-empty commentary."""
    t1 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    t2 = t1 + timedelta(days=1)
    t3 = t2 + timedelta(days=1)
    doc_2 = "TTEWSINGLES-----------RND1002800----------"

    def empty_404(doc: str, fetched_at: datetime) -> FetchSummary[tuple[int, str]]:
        return FetchSummary(
            key=(EVENT_ID, doc),
            status_code=404,
            received_count=0,
            payload_sha256=EMPTY_SHA256,
            fetched_at=fetched_at,
            error=None,
            reason="stale",
        )

    # new -> 404 [] lands, then 404 [] -> 200 non-empty updates.
    land_match_commentary_chunk(db_connection, [(empty_404(doc_2, t1), [])])
    payload = [_line(1, "SL", "Start of Match")]
    s2 = FetchSummary(
        key=(EVENT_ID, doc_2),
        status_code=200,
        received_count=1,
        payload_sha256=hashlib.sha256(json.dumps(payload).encode()).hexdigest(),
        fetched_at=t2,
        error=None,
        reason="live",
    )
    land_match_commentary_chunk(db_connection, [(s2, payload)])

    # Non-empty DOC_1 at t1, then mixed chunk: DOC_1 gets 404 [] at t3 (blocked)
    # while doc_3 gets 200 non-empty at t3 (lands).
    s1 = FetchSummary(
        key=(EVENT_ID, DOC_1),
        status_code=200,
        received_count=1,
        payload_sha256=hashlib.sha256(json.dumps(payload).encode()).hexdigest(),
        fetched_at=t1,
        error=None,
        reason="live",
    )
    land_match_commentary_chunk(db_connection, [(s1, payload)])

    doc_3 = "TTEWSINGLES-----------RND1002900----------"
    s3 = FetchSummary(
        key=(EVENT_ID, doc_3),
        status_code=200,
        received_count=1,
        payload_sha256=hashlib.sha256(json.dumps(payload).encode()).hexdigest(),
        fetched_at=t3,
        error=None,
        reason="new",
    )
    with caplog.at_level(logging.WARNING):
        land_match_commentary_chunk(
            db_connection,
            [(empty_404(DOC_1, t3), []), (s3, payload)],
        )

    preserved = db_connection.execute(
        "SELECT payload, received_count, http_status, fetched_at "
        "FROM ingest_wtt.match_commentary_raw "
        "WHERE event_id = %s AND document_code = %s;",
        (EVENT_ID, DOC_1),
    ).fetchone()
    assert preserved == {
        "payload": payload,
        "received_count": 1,
        "http_status": 200,
        "fetched_at": t1,
    }
    doc3_row = db_connection.execute(
        "SELECT received_count FROM ingest_wtt.match_commentary_raw "
        "WHERE event_id = %s AND document_code = %s;",
        (EVENT_ID, doc_3),
    ).fetchone()
    assert doc3_row == {"received_count": 1}
    assert any(
        r.getMessage() == "match_commentary_empty_overwrite_blocked"
        for r in caplog.records
    )

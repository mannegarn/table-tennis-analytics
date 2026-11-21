"""Integration tests: seeding ingest_wtt.match_details_raw from backup JSONs."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from psycopg import Connection
from psycopg.types.json import Jsonb

from tt_stats.matches.backup_seed import SeedResult, seed_match_details_backup

pytestmark = pytest.mark.integration

EVENT_ID = 9_400_223
DOC_404 = "TTEMSINGLES-----------R64-000100----------"
DOC_200 = "TTEMSINGLES-----------FNL-000100----------"
DOC_NEW = "TTEWSINGLES-----------FNL-000100----------"


def _match_payload(document_code: str, player_id: str) -> dict[str, Any]:
    return {
        "eventId": str(EVENT_ID),
        "documentCode": document_code,
        "resultStatus": "OFFICIAL",
        "competitiors": [{"players": [{"playerId": player_id}]}],
    }


def test_seed_backup_loads_raw_replaces_404_preserves_live_and_skips_unpack(
    db_connection: Connection[Any],
    tmp_path: Path,
) -> None:
    now = datetime.now(tz=UTC)
    empty_sha = hashlib.sha256(b"{}").hexdigest()
    live_payload = {"eventId": str(EVENT_ID), "documentCode": DOC_200, "live": True}
    live_sha = hashlib.sha256(json.dumps(live_payload).encode("utf-8")).hexdigest()

    # Existing 404 row should be replaced; existing live 200 row must be preserved.
    db_connection.execute(
        """
        INSERT INTO ingest_wtt.match_details_raw (
            event_id, document_code, payload, http_status,
            payload_sha256, source, fetched_at
        )
        VALUES
            (%s, %s, %s, 404, %s, 'wtt_matchcentre', %s),
            (%s, %s, %s, 200, %s, 'wtt_matchcentre', %s);
        """,
        (
            EVENT_ID,
            DOC_404,
            Jsonb({}),
            empty_sha,
            now,
            EVENT_ID,
            DOC_200,
            Jsonb(live_payload),
            live_sha,
            now,
        ),
    )

    backup_file = tmp_path / f"{EVENT_ID}_match_details.json"
    backup_file.write_text(
        json.dumps(
            [
                _match_payload(DOC_404, "1001"),
                _match_payload(DOC_200, "1002"),
                _match_payload(DOC_NEW, "1003"),
            ]
        ),
        encoding="utf-8",
    )

    first = seed_match_details_backup(db_connection, tmp_path, batch_size=2)
    assert first == SeedResult(files=1, matches_seen=3, rows_written=2)

    # Re-running is idempotent: all rows are now 200 with non-empty payloads.
    second = seed_match_details_backup(db_connection, tmp_path, batch_size=2)
    assert second == SeedResult(files=1, matches_seen=3, rows_written=0)

    rows = db_connection.execute(
        """
        SELECT document_code, http_status, source, payload
        FROM ingest_wtt.match_details_raw
        WHERE event_id = %s
        ORDER BY document_code;
        """,
        (EVENT_ID,),
    ).fetchall()
    assert [(r["document_code"], r["http_status"], r["source"]) for r in rows] == [
        (DOC_200, 200, "wtt_matchcentre"),
        (DOC_404, 200, "wtt_backup"),
        (DOC_NEW, 200, "wtt_backup"),
    ]
    assert rows[0]["payload"] == live_payload
    assert rows[1]["payload"]["resultStatus"] == "OFFICIAL"
    assert rows[2]["payload"]["resultStatus"] == "OFFICIAL"

    # Strictly raw load: nothing unpacked into match_details_players_unpacked.
    unpacked_count = db_connection.execute(
        "SELECT count(*) AS n FROM ingest_wtt.match_details_players_unpacked;"
    ).fetchone()
    assert unpacked_count is not None
    assert unpacked_count["n"] == 0

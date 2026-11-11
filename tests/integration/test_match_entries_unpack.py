"""Integration tests: a landed match list unpacks into one row per match.

Runs against ``tt_stats_test``, truncated around every test by the autouse
fixture, so nothing here needs cleanup. Event ids sit in the private range the
events tests use, so a test can never touch collected data.

The cases are the measurements that shaped ADR 0010: the document code is what
identifies a match, and the upstream id - which repeats inside an event and
across events, and may be 0 - is left in the payload.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import Connection, errors

from tt_stats.matches.collect.match_entries import MatchEntriesSummary
from tt_stats.matches.land import land_match_entries
from tt_stats.matches.land import unpack as matches_unpack
from tt_stats.matches.land.unpack import unpack_events

pytestmark = pytest.mark.integration

EVENT_ID = 9_100_398
OTHER_EVENT_ID = 9_100_399
MATCH_ID = 5_000_001

MEN_SINGLES = "TTEMSINGLES-----------R64-000100--------"
WOMEN_SINGLES = "TTEWSINGLES-----------R64-000100--------"
TEAM_CARD = "TTEMTEAM--------------FNL-000100------"

#: A real payload is an array; a drift test deliberately lands an object.
Payload = list[dict[str, Any]] | dict[str, Any]


def _row(**overrides: Any) -> dict[str, Any]:
    """One match row as the endpoint sends it."""
    row: dict[str, Any] = {
        "id": MATCH_ID,
        "eventId": str(EVENT_ID),
        "documentCode": MEN_SINGLES,
        "subEventType": "Men Singles",
        "fullResults": "OFFICIAL",
        "startDateLocal": "2025-09-29T18:35:00",
        "match_card": None,
        "extended_info": None,
        "messagePayload": None,
    }
    row.update(overrides)
    return row


def _without(key: str, **overrides: Any) -> dict[str, Any]:
    """The same row with one key removed, as an upstream rename would."""
    row = _row(**overrides)
    row.pop(key, None)
    return row


def _summary(event_id: int, payload: Payload) -> MatchEntriesSummary:
    body = json.dumps(payload).encode("utf-8")
    return MatchEntriesSummary(
        event_id=event_id,
        status_code=200,
        received_count=len(payload) if isinstance(payload, list) else 0,
        payload_sha256=hashlib.sha256(body).hexdigest(),
        fetched_at=datetime.now(tz=UTC),
        error=None,
        reason="new",
    )


def _land(connection: Connection[Any], event_id: int, payload: Payload) -> None:
    land_match_entries(connection, _summary(event_id, payload), payload)


def _unpacked(
    connection: Connection[Any],
    event_id: int = EVENT_ID,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT event_id, document_code, first_seen_at, last_seen_at
        FROM ingest.match_entries_unpacked
        WHERE event_id = %s
        ORDER BY document_code;
        """,
        (event_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _raw_count(connection: Connection[Any], event_id: int = EVENT_ID) -> int:
    row = connection.execute(
        """
        SELECT count(*) AS n
        FROM ingest.match_entries_payloads_raw
        WHERE event_id = %s;
        """,
        (event_id,),
    ).fetchone()
    return int(row["n"]) if row else 0


def test_landing_unpacks_one_row_per_match(db_connection: Connection[Any]) -> None:
    _land(
        db_connection,
        EVENT_ID,
        [
            _row(documentCode=MEN_SINGLES),
            _row(id=MATCH_ID + 1, documentCode=WOMEN_SINGLES),
        ],
    )

    assert [row["document_code"] for row in _unpacked(db_connection)] == [
        MEN_SINGLES,
        WOMEN_SINGLES,
    ]


def test_unpacked_columns_match_the_payload(
    db_connection: Connection[Any],
) -> None:
    _land(db_connection, EVENT_ID, [_row()])

    row = _unpacked(db_connection)[0]

    assert row["event_id"] == EVENT_ID
    assert row["document_code"] == MEN_SINGLES
    assert row["first_seen_at"] is not None
    assert row["last_seen_at"] >= row["first_seen_at"]


def test_a_team_card_without_an_id_or_a_date_is_kept(
    db_connection: Connection[Any],
) -> None:
    """Event 2345 sends 183 tie cards with no id and no date."""
    _land(
        db_connection,
        EVENT_ID,
        [
            _row(
                id=0,
                documentCode=TEAM_CARD,
                subEventType="Men's Team",
                startDateLocal="",
            )
        ],
    )

    assert _unpacked(db_connection)[0]["document_code"] == TEAM_CARD


def test_two_document_codes_sharing_one_id_are_two_matches(
    db_connection: Connection[Any],
) -> None:
    """Event 2411 sends id 59820 as both QFNL000100 and QFNL000200."""
    _land(
        db_connection,
        EVENT_ID,
        [
            _row(documentCode=MEN_SINGLES),
            _row(documentCode=WOMEN_SINGLES),
        ],
    )

    assert len(_unpacked(db_connection)) == 2


def test_the_same_match_id_in_two_events_is_kept_twice(
    db_connection: Connection[Any],
) -> None:
    """132 ids appear under two or three events, so the key needs the event."""
    _land(db_connection, EVENT_ID, [_row(eventId=str(EVENT_ID))])
    _land(db_connection, OTHER_EVENT_ID, [_row(eventId=str(OTHER_EVENT_ID))])

    assert len(_unpacked(db_connection, EVENT_ID)) == 1
    assert len(_unpacked(db_connection, OTHER_EVENT_ID)) == 1
    assert _unpacked(db_connection, OTHER_EVENT_ID)[0]["event_id"] == OTHER_EVENT_ID


def test_a_repeated_document_code_unpacks_once(
    db_connection: Connection[Any],
) -> None:
    """DISTINCT ON collapses a duplicate code instead of failing the upsert."""
    _land(db_connection, EVENT_ID, [_row(), _row()])

    assert len(_unpacked(db_connection)) == 1


def test_re_running_the_unpack_is_idempotent(
    db_connection: Connection[Any],
) -> None:
    _land(
        db_connection,
        EVENT_ID,
        [_row(), _row(documentCode=WOMEN_SINGLES, id=MATCH_ID + 1)],
    )
    before = _unpacked(db_connection)

    time.sleep(0.05)
    results = unpack_events([EVENT_ID])

    after = _unpacked(db_connection)

    assert results == [(EVENT_ID, 2)]
    assert len(after) == len(before)
    for old, new in zip(before, after, strict=True):
        assert new["first_seen_at"] == old["first_seen_at"], (
            "first_seen_at is history and must not move"
        )
        assert new["last_seen_at"] > old["last_seen_at"], (
            "last_seen_at must advance on every unpack"
        )


def test_dry_run_reports_counts_but_writes_nothing(
    db_connection: Connection[Any],
) -> None:
    _land(db_connection, EVENT_ID, [_row()])
    db_connection.execute(
        "DELETE FROM ingest.match_entries_unpacked WHERE event_id = %s;",
        (EVENT_ID,),
    )

    results = unpack_events([EVENT_ID], dry_run=True)

    assert results == [(EVENT_ID, 1)]
    assert _unpacked(db_connection) == []


def test_the_backfill_covers_every_landed_event(
    db_connection: Connection[Any],
) -> None:
    _land(db_connection, EVENT_ID, [_row()])
    _land(db_connection, OTHER_EVENT_ID, [_row()])
    db_connection.execute("DELETE FROM ingest.match_entries_unpacked;")

    results = unpack_events()

    assert dict(results) == {EVENT_ID: 1, OTHER_EVENT_ID: 1}
    assert len(_unpacked(db_connection, EVENT_ID)) == 1
    assert len(_unpacked(db_connection, OTHER_EVENT_ID)) == 1


def test_an_event_without_a_payload_unpacks_to_nothing(
    db_connection: Connection[Any],
) -> None:
    assert unpack_events([EVENT_ID]) == [(EVENT_ID, 0)]
    assert _unpacked(db_connection) == []


def test_an_empty_match_list_is_a_success(
    db_connection: Connection[Any],
) -> None:
    """2307 landed payloads hold ``[]``; zero rows unpacked is correct."""
    _land(db_connection, EVENT_ID, [])

    assert _unpacked(db_connection) == []
    assert _raw_count(db_connection) == 1


def test_landing_rolls_back_when_the_document_code_is_missing(
    db_connection: Connection[Any],
) -> None:
    with pytest.raises(errors.NotNullViolation):
        _land(db_connection, EVENT_ID, [_without("documentCode")])

    assert _raw_count(db_connection) == 0, "the raw upsert must roll back with it"
    assert _unpacked(db_connection) == []


def test_landing_rolls_back_when_the_payload_is_not_an_array(
    db_connection: Connection[Any],
) -> None:
    """``jsonb_array_elements`` raises here instead of silently unpacking nothing."""
    with pytest.raises(psycopg.Error):
        _land(db_connection, EVENT_ID, {"rows": [_row()]})

    assert _raw_count(db_connection) == 0
    assert _unpacked(db_connection) == []


def test_the_schema_keys_on_the_event_and_the_document_code(
    db_connection: Connection[Any],
) -> None:
    rows = db_connection.execute(
        """
        SELECT a.attname AS column_name
        FROM pg_index AS i
        JOIN pg_attribute AS a
          ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = 'ingest.match_entries_unpacked'::regclass
          AND i.indisprimary
        ORDER BY a.attname;
        """
    ).fetchall()

    assert [row["column_name"] for row in rows] == ["document_code", "event_id"]


def test_the_unpacked_table_holds_identity_and_presence_only(
    db_connection: Connection[Any],
) -> None:
    columns = db_connection.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'ingest'
          AND table_name = 'match_entries_unpacked'
        ORDER BY ordinal_position;
        """
    ).fetchall()

    assert [row["column_name"] for row in columns] == [
        "event_id",
        "document_code",
        "first_seen_at",
        "last_seen_at",
    ]


def test_standalone_unpack_raises_and_rolls_back_when_sql_writes_nothing(
    db_connection: Connection[Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``tt-matches-unpack`` must enforce the same completeness check as landing."""
    _land(db_connection, EVENT_ID, [_row()])
    db_connection.execute(
        "DELETE FROM ingest.match_entries_unpacked WHERE event_id = %s;",
        (EVENT_ID,),
    )

    broken_sql = tmp_path / "broken_unpack.sql"
    broken_sql.write_text(
        """
        INSERT INTO ingest.match_entries_unpacked (event_id, document_code)
        SELECT event_id, 'never'
        FROM ingest.match_entries_payloads_raw
        WHERE event_id = %(event_id)s AND false;
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(matches_unpack, "UNPACK_SQL_PATH", broken_sql)

    with pytest.raises(RuntimeError, match="Unpack validation failed"):
        unpack_events([EVENT_ID])

    assert _unpacked(db_connection) == []

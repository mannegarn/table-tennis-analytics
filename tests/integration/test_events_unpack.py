"""Integration test: landing unpacks in the same transaction, repeats are safe.

Runs against the isolated ``tt_stats_test`` database, so the committed fixture
can be landed with its real EventIds. The module skips itself while
``unpack_events.sql`` is still a scaffold of comments.

The drift tests below are the point of the post-unpack assertion: a payload that
silently yields nothing, or that loses a core field, must abort the landing
transaction instead of committing a year that looks successful.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from psycopg import Connection, errors

from tt_stats.events.collect.events_by_year import YearPayloadSummary
from tt_stats.events.land import land_events_by_year
from tt_stats.events.land.unpack import load_unpack_sql, unpack_years

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        load_unpack_sql() is None,
        reason="unpack_events.sql is still a scaffold",
    ),
]

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "events_2023.json"
TEST_YEAR = 2023
KNOWN_EVENT_ID = 2629

# Core fields the unpack statement reads with ``->>``. Removing one is the
# cheapest stand-in for WTT renaming it: the key simply is not there any more.
CORE_FIELDS = ("EventId", "EventName", "StartDateTime", "EndDateTime")


def _payload() -> Any:
    """Return the committed fixture payload unchanged."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _digest(payload: Any) -> str:
    """Fingerprint a synthetic payload the way the collector would."""
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _summary(
    payload: Any,
    digest: str,
    *,
    received_count: int | None = None,
) -> YearPayloadSummary:
    """Build the summary the collector would have produced for ``payload``.

    ``received_count`` is overridable so a drift test can describe a payload
    whose rows were still counted upstream while the stored JSON no longer
    contains them.
    """
    envelope = payload[0]
    return YearPayloadSummary(
        year=TEST_YEAR,
        status_code=200,
        source_final_count=envelope.get("Count"),
        source_original_count=(
            int(envelope["Original_Count"]) if "Original_Count" in envelope else None
        ),
        received_count=(
            len(envelope["rows"]) if received_count is None else received_count
        ),
        payload_sha256=digest,
        fetched_at=datetime.now(tz=UTC),
        error=None,
    )


def _payload_without(field: str) -> Any:
    """Return the fixture with ``field`` removed from every event row."""
    payload = _payload()
    for row in payload[0]["rows"]:
        row.pop(field, None)
    return payload


def _payload_with_renamed_rows_key() -> Any:
    """Return the fixture with ``'rows'`` renamed, as an upstream change would."""
    envelope = _payload()[0]
    return [
        {
            "Count": envelope["Count"],
            "Original_Count": envelope["Original_Count"],
            "Data": envelope["rows"],
        }
    ]


def _raw_rows(connection: Connection[Any]) -> int:
    """Number of landed raw payloads for the test year (0 means rolled back)."""
    row = connection.execute(
        "SELECT count(*) AS n FROM ingest.events_by_year_raw WHERE year = %s;",
        (TEST_YEAR,),
    ).fetchone()
    return int(row["n"]) if row else 0


def _counts(connection: Connection[Any]) -> dict[str, int]:
    row = connection.execute(
        """
        SELECT count(*) AS rows, count(DISTINCT event_id) AS events
        FROM ingest.events_unpacked
        WHERE source_year = %s;
        """,
        (TEST_YEAR,),
    ).fetchone()
    return dict(row) if row else {"rows": 0, "events": 0}


def _sightings(
    connection: Connection[Any],
) -> dict[str, tuple[datetime, datetime]]:
    """Map ``"year:event_id"`` to its ``(first_seen_at, last_seen_at)`` pair."""
    rows = connection.execute(
        """
        SELECT source_year, event_id, first_seen_at, last_seen_at
        FROM ingest.events_unpacked
        WHERE source_year = %s;
        """,
        (TEST_YEAR,),
    ).fetchall()
    return {
        f"{row['source_year']}:{row['event_id']}": (
            row["first_seen_at"],
            row["last_seen_at"],
        )
        for row in rows
    }


@pytest.fixture
def landed_year() -> int:
    """Land the fixture year; the truncate fixture removes every trace of it."""
    payload = _payload()
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    land_events_by_year(_summary(payload, digest), payload)
    return TEST_YEAR


def test_landing_unpacks_in_the_same_transaction(
    landed_year: int,
    db_connection: Connection[Any],
) -> None:
    payload = _payload()
    expected = len({row["EventId"] for row in payload[0]["rows"]})

    assert _counts(db_connection) == {"rows": expected, "events": expected}


def test_unpacked_columns_match_the_payload(
    landed_year: int,
    db_connection: Connection[Any],
) -> None:
    row = db_connection.execute(
        """
        SELECT event_name, start_date, end_date, source_year,
               first_seen_at, last_seen_at
        FROM ingest.events_unpacked
        WHERE source_year = %s AND event_id = %s;
        """,
        (TEST_YEAR, KNOWN_EVENT_ID),
    ).fetchone()

    assert row is not None
    assert row["event_name"] == "Singapore Smash 2023"
    assert str(row["start_date"]) == "2023-03-07"
    assert str(row["end_date"]) == "2023-03-19"
    assert row["source_year"] == TEST_YEAR
    assert row["first_seen_at"] is not None
    assert row["last_seen_at"] >= row["first_seen_at"]


def test_re_running_the_statement_for_one_year_is_idempotent(
    landed_year: int,
    db_connection: Connection[Any],
) -> None:
    first = unpack_years([TEST_YEAR])
    second = unpack_years([TEST_YEAR])
    counts = _counts(db_connection)

    assert first == second
    assert counts["rows"] == counts["events"]


def test_re_landing_a_year_advances_last_seen_at_only(
    landed_year: int,
    db_connection: Connection[Any],
) -> None:
    """Presence tracking: first_seen_at is history, last_seen_at keeps moving."""
    before = _sightings(db_connection)
    payload = _payload()

    land_events_by_year(
        _summary(payload, hashlib.sha256(FIXTURE.read_bytes()).hexdigest()),
        payload,
    )

    after = _sightings(db_connection)

    assert set(after) == set(before), "re-landing must not add or drop rows"
    for key, (first, last) in after.items():
        assert first == before[key][0], f"first_seen_at moved for {key}"
        assert last > before[key][1], f"last_seen_at did not advance for {key}"


def test_dry_run_reports_counts_but_rolls_back(
    landed_year: int,
    db_connection: Connection[Any],
) -> None:
    before = _counts(db_connection)

    unpack_years([TEST_YEAR], dry_run=True)

    assert _counts(db_connection) == before


def test_landing_writes_one_row_per_distinct_event_id(
    landed_year: int,
    db_connection: Connection[Any],
) -> None:
    """The count assertion compares distinct ids, not raw items.

    A year that repeats an event word-for-word (2021, 2022 and 2026 all do) must
    still land, so the check in the landing path de-duplicates first.
    """
    rows = _payload()[0]["rows"]
    distinct = len({row["EventId"] for row in rows})

    assert _raw_rows(db_connection) == 1
    assert _counts(db_connection) == {"rows": distinct, "events": distinct}


def test_landing_raises_and_rolls_back_when_the_rows_key_is_renamed(
    db_connection: Connection[Any],
) -> None:
    """The classic silent failure: ``jsonb_array_elements(NULL)`` returns 0 rows."""
    payload = _payload_with_renamed_rows_key()
    # Counted upstream from the original payload, so the drift is still visible.
    summary = _summary(
        payload,
        _digest(payload),
        received_count=len(_payload()[0]["rows"]),
    )

    with pytest.raises(RuntimeError, match="Unpack validation failed"):
        land_events_by_year(summary, payload)

    assert _raw_rows(db_connection) == 0, "the raw upsert must roll back with it"
    assert _counts(db_connection) == {"rows": 0, "events": 0}


@pytest.mark.parametrize("field", CORE_FIELDS)
def test_landing_raises_and_rolls_back_when_a_core_field_is_missing(
    db_connection: Connection[Any],
    field: str,
) -> None:
    """A missing core field becomes a NULL, which the schema now refuses.

    ``EventId``, ``EventName``, ``StartDateTime`` and ``EndDateTime`` all map to
    NOT NULL columns, so the year is neither accepted nor half-landed.
    """
    payload = _payload_without(field)
    summary = _summary(payload, _digest(payload))

    with pytest.raises((RuntimeError, errors.NotNullViolation)):
        land_events_by_year(summary, payload)

    assert _raw_rows(db_connection) == 0, "the raw upsert must roll back with it"
    assert _counts(db_connection) == {"rows": 0, "events": 0}


def test_landing_raises_when_the_summary_describes_a_different_payload(
    db_connection: Connection[Any],
) -> None:
    """An unexplained count mismatch is drift too, even if the unpack wrote rows."""
    payload = _payload()
    summary = _summary(payload, _digest(payload), received_count=1)

    with pytest.raises(RuntimeError, match="Unpack validation failed"):
        land_events_by_year(summary, payload)

    assert _raw_rows(db_connection) == 0
    assert _counts(db_connection) == {"rows": 0, "events": 0}

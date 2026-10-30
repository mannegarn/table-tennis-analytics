"""Integration tests: landing is atomic and failures are still audited.

Runs against the isolated ``tt_stats_test`` database, so the committed 2023
fixture can be landed unchanged: no id rewriting, and no cleanup sweeps that
risk real rows.

Besides the happy path, this module pins the two failure-mode invariants:

* a landing that fails midway leaves no rows behind in either ingest table;
* the failure is still visible afterwards, because ``ingest.ingest_runs`` is
  written by the orchestrator in its own transaction.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from psycopg import Connection, errors

from tt_stats.core.clients.wtt_api import WttClient
from tt_stats.core.ingest_runs import (
    SOURCE_WTT_EVENTCALENDAR,
    STATUS_FAILED,
    record_run,
)
from tt_stats.events.collect.events_by_year import (
    YearPayloadSummary,
    collect_events_by_year,
)
from tt_stats.events.land import land_events_by_year
from tt_stats.events.land.unpack import load_unpack_sql

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        load_unpack_sql() is None,
        reason="unpack_events.sql is still a scaffold",
    ),
]

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "events_2023.json"
TEST_YEAR = 2023
NAME_CHECK = "events_unpacked_name_check"


def _summary(payload: Any, digest: str) -> YearPayloadSummary:
    envelope = payload[0]
    return YearPayloadSummary(
        year=TEST_YEAR,
        status_code=200,
        source_final_count=envelope["Count"],
        source_original_count=int(envelope["Original_Count"]),
        received_count=len(envelope["rows"]),
        payload_sha256=digest,
        fetched_at=datetime.now(tz=UTC),
        error=None,
    )


def test_landing_upserts_one_row_and_is_idempotent(
    db_connection: Connection[Any],
) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    summary = _summary(payload, digest)

    land_events_by_year(summary, payload)
    land_events_by_year(summary, payload)

    row = db_connection.execute(
        """
        SELECT payload, http_status, source_final_count,
               source_original_count, received_count, payload_sha256
        FROM ingest.events_by_year_raw
        WHERE year = %s;
        """,
        (TEST_YEAR,),
    ).fetchone()
    count = db_connection.execute(
        "SELECT count(*) AS n FROM ingest.events_by_year_raw WHERE year = %s;",
        (TEST_YEAR,),
    ).fetchone()

    assert row is not None
    assert row["payload"] == payload
    assert row["http_status"] == 200
    assert row["source_final_count"] == payload[0]["Count"]
    assert row["source_original_count"] == int(payload[0]["Original_Count"])
    assert row["received_count"] == len(payload[0]["rows"])
    assert row["payload_sha256"] == digest
    assert count == {"n": 1}


def _malformed_payload() -> Any:
    """Return the fixture with every ``EventName`` blanked out.

    An empty string satisfies the JSON contract the collector checks, but not
    ``events_unpacked_name_check``: the failure therefore happens inside the
    unpack, i.e. after the raw upsert has already written a row. That is exactly
    the moment where a non-atomic landing would leave orphans behind.
    """
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for row in payload[0]["rows"]:
        row["EventName"] = ""
    return payload


def _raw_count(connection: Connection[Any]) -> int:
    row = connection.execute(
        "SELECT count(*) AS n FROM ingest.events_by_year_raw WHERE year = %s;",
        (TEST_YEAR,),
    ).fetchone()
    return int(row["n"]) if row else 0


def _unpacked_count(connection: Connection[Any]) -> int:
    row = connection.execute(
        "SELECT count(*) AS n FROM ingest.events_unpacked WHERE source_year = %s;",
        (TEST_YEAR,),
    ).fetchone()
    return int(row["n"]) if row else 0


def _last_run(connection: Connection[Any]) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT status, error
        FROM ingest.ingest_runs
        WHERE source = %s AND entity_key = %s
        ORDER BY started_at DESC, run_id DESC
        LIMIT 1;
        """,
        (SOURCE_WTT_EVENTCALENDAR, str(TEST_YEAR)),
    ).fetchone()
    assert row is not None, "the failed attempt was never recorded"
    return dict(row)


def test_landing_raises_on_a_constraint_violation_and_rolls_back(
    db_connection: Connection[Any],
) -> None:
    """The lander itself propagates the error, having rolled both tables back."""
    payload = _malformed_payload()
    summary = _summary(payload, hashlib.sha256(FIXTURE.read_bytes()).hexdigest())

    with pytest.raises(errors.CheckViolation, match=NAME_CHECK):
        land_events_by_year(summary, payload)

    assert _raw_count(db_connection) == 0, "the raw upsert must roll back"
    assert _unpacked_count(db_connection) == 0, "unpacked rows must roll back"


async def test_landing_failure_rolls_back_cleanly_and_records_failed_run(
    db_connection: Connection[Any],
) -> None:
    """A failed landing leaves no orphan rows but still leaves an audit row.

    The failure is triggered through the real collector entry point, so the
    transaction boundary under test is the one production uses: the lander
    commits or rolls back, then the orchestrator records the attempt in its own
    transaction.
    """

    payload = _malformed_payload()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with WttClient(transport=httpx.MockTransport(handler)) as client:
        summaries = await collect_events_by_year(
            [TEST_YEAR],
            client=client,
            land=land_events_by_year,
            record=record_run,
        )

    summary = summaries[0]
    assert summary.error is not None
    assert summary.error.startswith("landing failed:")
    assert NAME_CHECK in summary.error

    assert _raw_count(db_connection) == 0
    assert _unpacked_count(db_connection) == 0

    run = _last_run(db_connection)
    assert run["status"] == STATUS_FAILED
    assert run["error"]
    assert NAME_CHECK in run["error"]

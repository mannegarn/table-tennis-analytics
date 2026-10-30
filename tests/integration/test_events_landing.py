"""Integration test: one year lands, upserts and stays a single row.

Runs against the isolated ``tt_stats_test`` database, so the committed 2023
fixture can be landed unchanged: no id rewriting, and no cleanup sweeps that
risk real rows.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from psycopg import Connection

from tt_stats.events.collect.events_by_year import YearPayloadSummary
from tt_stats.events.land import land_events_by_year

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "events_2023.json"
TEST_YEAR = 2023


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

"""Integration test: one year lands, upserts and stays a single row."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from pydantic import ValidationError

from tt_stats.core.config import get_settings
from tt_stats.core.db import get_connection
from tt_stats.core.migrations import apply_migrations
from tt_stats.events.collect.events_by_year import YearPayloadSummary
from tt_stats.events.land import land_events_by_year

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "events_2023.json"
TEST_YEAR = 2099


def _require_database() -> None:
    try:
        settings = get_settings()
    except ValidationError as error:
        pytest.skip(f"Database settings are incomplete: {error}")

    try:
        with psycopg.connect(settings.dsn):
            pass
    except psycopg.OperationalError as error:
        pytest.skip(f"PostgreSQL not available: {error}")


def _summary(payload: Any, digest: str) -> YearPayloadSummary:
    envelope = payload[0]
    return YearPayloadSummary(
        year=TEST_YEAR,
        status_code=200,
        api_count=envelope["Count"],
        original_count=int(envelope["Original_Count"]),
        row_count=len(envelope["rows"]),
        payload_sha256=digest,
        fetched_at=datetime.now(tz=UTC),
        error=None,
    )


def test_landing_upserts_one_row_and_is_idempotent() -> None:
    _require_database()
    apply_migrations()

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    summary = _summary(payload, digest)

    try:
        land_events_by_year(summary, payload)
        land_events_by_year(summary, payload)

        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT payload, http_status, api_count, original_count,
                       row_count, payload_sha256
                FROM ingest.events_by_year_raw
                WHERE year = %s;
                """,
                (TEST_YEAR,),
            ).fetchone()
            count = connection.execute(
                "SELECT count(*) AS n FROM ingest.events_by_year_raw WHERE year = %s;",
                (TEST_YEAR,),
            ).fetchone()

        assert row is not None
        assert row["payload"] == payload
        assert row["http_status"] == 200
        assert row["api_count"] == payload[0]["Count"]
        assert row["original_count"] == int(payload[0]["Original_Count"])
        assert row["row_count"] == len(payload[0]["rows"])
        assert row["payload_sha256"] == digest
        assert count == {"n": 1}
    finally:
        with get_connection() as connection:
            connection.execute(
                "DELETE FROM ingest.events_by_year_raw WHERE year = %s;",
                (TEST_YEAR,),
            )

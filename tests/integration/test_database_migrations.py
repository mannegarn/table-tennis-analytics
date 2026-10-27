"""Integration test: migrations create the ingestion-owned raw table."""

import psycopg
import pytest
from pydantic import ValidationError

from tt_stats.core.config import get_settings
from tt_stats.core.db import get_connection
from tt_stats.core.migrations import apply_migrations

pytestmark = pytest.mark.integration

EXPECTED_COLUMNS = {
    "year",
    "payload",
    "http_status",
    "api_count",
    "original_count",
    "row_count",
    "payload_sha256",
    "source",
    "fetched_at",
    "ingested_at",
}


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


def test_migrations_create_the_raw_table() -> None:
    _require_database()
    apply_migrations()
    assert apply_migrations() == []

    with get_connection() as connection:
        columns = connection.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'ingest'
              AND table_name = 'events_by_year_raw'
            ORDER BY ordinal_position;
            """
        ).fetchall()
        versions = connection.execute(
            "SELECT version FROM ingest.schema_version;"
        ).fetchall()
        schemas = connection.execute(
            """
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name IN ('ingest', 'analytics')
            ORDER BY schema_name;
            """
        ).fetchall()

    by_name = {row["column_name"]: row for row in columns}
    assert set(by_name) == EXPECTED_COLUMNS
    assert by_name["payload"]["data_type"] == "jsonb"
    assert by_name["year"]["is_nullable"] == "NO"
    assert by_name["payload_sha256"]["is_nullable"] == "NO"
    assert "001_events_raw" in {row["version"] for row in versions}
    assert [row["schema_name"] for row in schemas] == ["analytics", "ingest"]

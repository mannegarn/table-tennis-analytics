"""Integration test: migrations create the ingestion-owned raw table.

Runs against the isolated ``tt_stats_test`` database. The schema is already
migrated by the session fixture, so the second ``apply_migrations`` call below
is the assertion: nothing is pending.
"""

from typing import Any

import pytest
from psycopg import Connection

from tt_stats.core.migrations import apply_migrations

pytestmark = pytest.mark.integration

EXPECTED_COLUMNS = {
    "year",
    "payload",
    "http_status",
    "source_final_count",
    "source_original_count",
    "received_count",
    "payload_sha256",
    "source",
    "fetched_at",
    "ingested_at",
}


def test_migrations_create_the_raw_table(
    db_connection: Connection[Any],
) -> None:
    assert apply_migrations() == []

    columns = db_connection.execute(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'ingest'
          AND table_name = 'events_by_year_raw'
        ORDER BY ordinal_position;
        """
    ).fetchall()
    versions = db_connection.execute(
        "SELECT version FROM ingest.schema_version;"
    ).fetchall()
    schemas = db_connection.execute(
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
    applied = {row["version"] for row in versions}
    assert {
        "001_events_raw",
        "002_ingest_runs",
        "003_events_unpacked",
        "004_rename_count_columns",
    } <= applied
    assert [row["schema_name"] for row in schemas] == ["analytics", "ingest"]

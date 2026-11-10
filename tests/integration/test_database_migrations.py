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

EXPECTED_MATCH_ENTRY_RAW_COLUMNS = {
    "event_id",
    "payload",
    "http_status",
    "received_count",
    "payload_sha256",
    "source",
    "fetched_at",
    "ingested_at",
}

EXPECTED_MATCH_ENTRY_UNPACKED_COLUMNS = {
    "match_id",
    "event_id",
    "document_code",
    "sub_event_type",
    "full_results",
    "start_date_local",
    "first_seen_at",
    "last_seen_at",
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
    assert applied == {
        "001_events_raw",
        "002_ingest_runs",
        "003_events_unpacked",
        "004_match_entries",
        "005_event_year_range",
        "006_drop_inverted_date_check",
        "007_match_entries_pending",
    }
    assert [row["schema_name"] for row in schemas] == ["analytics", "ingest"]


def _columns(connection: Connection[Any], table: str) -> dict[str, Any]:
    """Return ``column_name -> row`` for one ``ingest`` table."""
    rows = connection.execute(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'ingest' AND table_name = %s
        ORDER BY ordinal_position;
        """,
        (table,),
    ).fetchall()
    return {row["column_name"]: row for row in rows}


def test_migrations_create_the_match_entries_tables(
    db_connection: Connection[Any],
) -> None:
    """The match-entries pair exists with the grain each table is named for."""
    raw = _columns(db_connection, "match_entries_payloads_raw")
    unpacked = _columns(db_connection, "match_entries_unpacked")

    assert set(raw) == EXPECTED_MATCH_ENTRY_RAW_COLUMNS
    assert set(unpacked) == EXPECTED_MATCH_ENTRY_UNPACKED_COLUMNS

    # The raw table is one row per event, so event_id is the key. The unpacked
    # table is one row per match, so match_id is.
    raw_key = db_connection.execute(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON kcu.constraint_name = tc.constraint_name
         AND kcu.table_schema = tc.table_schema
        WHERE tc.table_schema = 'ingest'
          AND tc.table_name = 'match_entries_payloads_raw'
          AND tc.constraint_type = 'PRIMARY KEY';
        """
    ).fetchall()
    assert [row["column_name"] for row in raw_key] == ["event_id"]

    unpacked_key = db_connection.execute(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON kcu.constraint_name = tc.constraint_name
         AND kcu.table_schema = tc.table_schema
        WHERE tc.table_schema = 'ingest'
          AND tc.table_name = 'match_entries_unpacked'
          AND tc.constraint_type = 'PRIMARY KEY';
        """
    ).fetchall()
    assert [row["column_name"] for row in unpacked_key] == ["match_id"]

    assert raw["payload"]["data_type"] == "jsonb"
    assert raw["event_id"]["data_type"] == "bigint"
    assert raw["event_id"]["is_nullable"] == "NO"

    # No timezone is invented: the API sends "2025-09-29T18:35:00" with none.
    assert unpacked["start_date_local"]["data_type"] == ("timestamp without time zone")
    assert unpacked["document_code"]["is_nullable"] == "NO"

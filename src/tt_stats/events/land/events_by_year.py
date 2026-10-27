"""Land one successfully fetched year into ingest.events_by_year_raw."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from psycopg.types.json import Jsonb

from tt_stats.core.db import get_connection

if TYPE_CHECKING:
    from tt_stats.events.collect.events_by_year import YearPayloadSummary


def land_events_by_year(
    summary: YearPayloadSummary,
    payload: Any,
) -> None:
    """Upsert one year's payload and fetch metadata in a single transaction.

    Failed fetches are not landed here. They belong in ``ingest_runs`` later,
    so a bad response can never overwrite a good payload.
    """
    if summary.error is not None:
        raise ValueError(f"cannot land a failed year: {summary.error}")
    if summary.status_code is None or summary.payload_sha256 is None:
        raise ValueError("summary is missing status or payload hash")

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO ingest.events_by_year_raw (
                year,
                payload,
                http_status,
                api_count,
                original_count,
                row_count,
                payload_sha256,
                fetched_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (year) DO UPDATE SET
                payload = EXCLUDED.payload,
                http_status = EXCLUDED.http_status,
                api_count = EXCLUDED.api_count,
                original_count = EXCLUDED.original_count,
                row_count = EXCLUDED.row_count,
                payload_sha256 = EXCLUDED.payload_sha256,
                fetched_at = EXCLUDED.fetched_at,
                ingested_at = now();
            """,
            (
                summary.year,
                Jsonb(payload),
                summary.status_code,
                summary.api_count,
                summary.original_count,
                summary.row_count,
                summary.payload_sha256,
                summary.fetched_at,
            ),
        )

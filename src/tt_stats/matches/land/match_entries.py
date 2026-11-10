"""Land one fetched match list into the raw table.

One event is one row and one transaction. The payload is stored exactly as it
arrived, including the common empty case: a ``[]`` body is a real observation
("WTT has no matches for this event"), and storing it is what stops the queue
asking again.

The unpack is deliberately **not** part of this function yet. It will run in
this same transaction once ``unpack_match_entries.sql`` exists, exactly as
``land_events_by_year`` runs ``unpack_events_year`` today (ADR 0003). Keeping
the landing correct and complete on its own means the collection stage can be
run, measured and inspected before any column mapping is committed to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from psycopg.types.json import Jsonb

from tt_stats.core.db import get_connection
from tt_stats.core.logging_config import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from tt_stats.matches.collect.match_entries import MatchEntriesSummary


def land_match_entries(
    summary: MatchEntriesSummary,
    payload: Any,
) -> None:
    """Upsert one event's match list into ``ingest.match_entries_payloads_raw``.

    Failed fetches are never landed. They belong in ``ingest_runs``, so a bad
    response cannot overwrite a good payload.

    Raises:
        ValueError: If ``summary`` describes a failed attempt or is incomplete.
    """
    if summary.error is not None:
        raise ValueError(f"cannot land a failed event: {summary.error}")
    if summary.status_code is None or summary.payload_sha256 is None:
        raise ValueError("summary is missing status or payload hash")

    with get_connection(autocommit=True) as connection, connection.transaction():
        connection.execute(
            """
            INSERT INTO ingest.match_entries_payloads_raw (
                event_id,
                payload,
                http_status,
                received_count,
                payload_sha256,
                fetched_at
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_id) DO UPDATE SET
                payload = EXCLUDED.payload,
                http_status = EXCLUDED.http_status,
                received_count = EXCLUDED.received_count,
                payload_sha256 = EXCLUDED.payload_sha256,
                fetched_at = EXCLUDED.fetched_at,
                ingested_at = now();
            """,
            (
                summary.event_id,
                Jsonb(payload),
                summary.status_code,
                summary.received_count,
                summary.payload_sha256,
                summary.fetched_at,
            ),
        )

    logger.info(
        "match_entries_landed",
        extra={
            "event_id": summary.event_id,
            "reason": summary.reason,
            "received_count": summary.received_count,
        },
    )

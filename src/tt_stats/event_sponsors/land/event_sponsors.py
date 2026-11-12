"""Land one fetched event-sponsor payload into ``ingest.event_sponsors_raw``.

One event is one row and one transaction. The payload is stored exactly as it
arrived, including the common empty case: a ``[]`` body is a real observation
("WTT lists no sponsors for this event"), and storing it is what stops the
pending queue asking again.

No SQL unpack runs here yet because no downstream scraping depends on sponsor
fields; the raw JSONB payload is the source of truth and can be unpacked later
or read directly by dbt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from psycopg.types.json import Jsonb

from tt_stats.core.logging_config import get_logger
from tt_stats.event_sponsors.land.unpack import unpack_event

logger = get_logger(__name__)

if TYPE_CHECKING:
    from psycopg import Connection

    from tt_stats.event_sponsors.collect.event_sponsors import EventSponsorsSummary


def land_event_sponsors(
    connection: Connection[Any],
    summary: EventSponsorsSummary,
    payload: Any,
) -> None:
    """Upsert one event's raw sponsor payload in a single transaction.

    The caller owns the connection (expected ``autocommit=True`` so each
    ``connection.transaction()`` commits or rolls back on its own across a
    multi-event run). Failed fetches are never landed here; they belong in
    ``ingest_runs`` so a bad response can never overwrite a good payload.

    Raises:
        ValueError: If ``summary`` describes a failed attempt or is incomplete.
    """
    if summary.error is not None:
        raise ValueError(f"cannot land a failed event: {summary.error}")
    if summary.status_code is None or summary.payload_sha256 is None:
        raise ValueError("summary is missing status or payload hash")

    with connection.transaction():
        connection.execute(
            """
            INSERT INTO ingest.event_sponsors_raw (
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

        unpacked = unpack_event(
            connection,
            summary.event_id,
            received_count=summary.received_count,
        )

    logger.info(
        "event_sponsors_landed",
        extra={
            "event_id": summary.event_id,
            "reason": summary.reason,
            "received_count": summary.received_count,
            "unpacked": unpacked,
        },
    )

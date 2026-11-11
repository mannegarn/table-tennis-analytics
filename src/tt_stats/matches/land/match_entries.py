"""Land one fetched match list into the raw table, then unpack it.

One event is one row and one transaction. The payload is stored exactly as it
arrived, including the common empty case: a ``[]`` body is a real observation
("WTT has no matches for this event"), and storing it is what stops the queue
asking again.

The unpack runs inside that same transaction, exactly as ``land_events_by_year``
runs ``unpack_events_year`` (ADR 0003), so a failed unpack rolls the raw write
back too and an event is never half-landed. The unpack is followed by an
integrity assertion, because the drift that matters most is silent in SQL:
``jsonb_array_elements`` yields zero rows for a payload that is not an array at
all, and a renamed key fills a column with NULLs. Both are caught here and
raised as ``RuntimeError``, which aborts the transaction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from psycopg.types.json import Jsonb

from tt_stats.core.logging_config import get_logger
from tt_stats.matches.land.unpack import unpack_event

logger = get_logger(__name__)

if TYPE_CHECKING:
    from psycopg import Connection

    from tt_stats.matches.collect.match_entries import MatchEntriesSummary


def land_match_entries(
    connection: Connection[Any],
    summary: MatchEntriesSummary,
    payload: Any,
) -> None:
    """Upsert one event's match list, then unpack it, in one transaction.

    The caller owns the connection (expected ``autocommit=True`` so each
    ``connection.transaction()`` commits or rolls back on its own across a
    multi-event run). Failed fetches are never landed. They belong in
    ``ingest_runs``, so a bad response cannot overwrite a good payload.

    Raises:
        ValueError: If ``summary`` describes a failed attempt or is incomplete.
        RuntimeError: If the unpack wrote a different number of rows than the
            payload implies, or wrote nothing for a payload that has matches.
            Both roll the whole landing back.
    """
    if summary.error is not None:
        raise ValueError(f"cannot land a failed event: {summary.error}")
    if summary.status_code is None or summary.payload_sha256 is None:
        raise ValueError("summary is missing status or payload hash")

    # An explicit transaction block on the caller's autocommit connection: the
    # commit happens here and nowhere else. Any exception inside the block - a
    # constraint violation, a failed cast, or the post-unpack assertion - rolls
    # back the raw upsert and every unpacked row together, so nothing is
    # half-landed and the connection stays reusable for the next event.
    with connection.transaction():
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

        unpacked = unpack_event(
            connection,
            summary.event_id,
            received_count=summary.received_count,
        )

    logger.info(
        "match_entries_landed",
        extra={
            "event_id": summary.event_id,
            "reason": summary.reason,
            "received_count": summary.received_count,
            "unpacked": unpacked,
        },
    )

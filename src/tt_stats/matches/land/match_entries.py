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

from tt_stats.core.db import get_connection
from tt_stats.core.logging_config import get_logger
from tt_stats.matches.land.unpack import unpack_event

logger = get_logger(__name__)

if TYPE_CHECKING:
    from psycopg import Connection

    from tt_stats.matches.collect.match_entries import MatchEntriesSummary

# What the unpack should have written for one event, worked out from the payload
# that is now sitting in the raw table. Deliberately mirrors
# unpack_match_entries.sql: the same array expansion and the same
# ``documentCode`` key, so a rename in one without the other shows up as a count
# mismatch rather than a silent difference.
#
#   items      elements of the payload array
#   documents  distinct document codes, which is what the statement writes
#
# A payload that is not an array raises here rather than reporting zero.
_EXPECTATION_SQL = """
SELECT
    count(*) AS items,
    count(DISTINCT e.value ->> 'documentCode') AS documents
FROM ingest.match_entries_payloads_raw AS r
CROSS JOIN LATERAL jsonb_array_elements(r.payload) AS e
WHERE r.event_id = %s;
"""


def land_match_entries(
    summary: MatchEntriesSummary,
    payload: Any,
) -> None:
    """Upsert one event's match list, then unpack it, in one transaction.

    Failed fetches are never landed. They belong in ``ingest_runs``, so a bad
    response cannot overwrite a good payload.

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

    # ``autocommit`` plus an explicit transaction block, rather than relying on
    # the connection context manager: the commit happens exactly here and
    # nowhere else. Any exception inside the block - a constraint violation, a
    # failed cast, or the post-unpack assertion - rolls back the raw upsert and
    # every unpacked row together, so nothing is half-landed.
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

        unpacked = unpack_event(connection, summary.event_id)
        _assert_unpacked(connection, event_id=summary.event_id, unpacked=unpacked)

    logger.info(
        "match_entries_landed",
        extra={
            "event_id": summary.event_id,
            "reason": summary.reason,
            "received_count": summary.received_count,
            "unpacked": unpacked,
        },
    )


def _assert_unpacked(
    connection: Connection[Any],
    *,
    event_id: int,
    unpacked: int,
) -> None:
    """Raise unless the unpack wrote one row per distinct document code.

    An empty payload is a success and stays one: ``0 == 0``. Anything else means
    the statement and the payload disagree, and the transaction must roll back.
    """
    row = connection.execute(_EXPECTATION_SQL, (event_id,)).fetchone()
    items = int(row["items"]) if row else 0
    expected = int(row["documents"]) if row else 0
    if unpacked != expected:
        raise RuntimeError(
            "Unpack validation failed: "
            f"event {event_id} holds {items} items with {expected} distinct "
            f"document codes, but the unpack touched {unpacked} rows"
        )

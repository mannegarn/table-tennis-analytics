"""Land one successfully fetched year into the raw and unpacked tables.

Both writes share one transaction: the payload is upserted and the same
transaction then runs the unpack statement from ``sql/unpack_events.sql``. A
failed unpack therefore rolls back the raw row as well, so a year is never
half-landed (ADR 0003).

The unpack is followed by an integrity assertion, because the unpack statement
cannot fail on its own for the drift that matters most. ``payload -> 'rows'``
returning NULL yields zero rows instead of an error, and a renamed field such as
``EventName`` would insert NULL if the column allowed it. Both cases are silent
in SQL, so they are checked here and raised as ``RuntimeError`` instead, which
aborts the transaction and keeps the bad year out of the database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from psycopg.types.json import Jsonb

from tt_stats.core.logging_config import get_logger
from tt_stats.events.land.unpack import unpack_events_year

logger = get_logger(__name__)

if TYPE_CHECKING:
    from psycopg import Connection

    from tt_stats.events.collect.events_by_year import YearPayloadSummary


def land_events_by_year(
    connection: Connection[Any],
    summary: YearPayloadSummary,
    payload: Any,
) -> None:
    """Upsert one year's payload and unpack it in a single transaction.

    The caller owns the connection (expected ``autocommit=True`` so each
    ``connection.transaction()`` commits or rolls back on its own across a
    multi-year run). The unpack statement runs inside that same transaction,
    after the insert, so the columns can always be rebuilt from the raw row
    that produced them. Its output is then counted and compared against the
    payload, so a rename or a dropped key aborts the transaction instead of
    committing a year that merely looks successful.

    Failed fetches are not landed here. They belong in ``ingest_runs``, so a bad
    response can never overwrite a good payload.

    Raises:
        ValueError: If ``summary`` describes a failed attempt or is incomplete.
        RuntimeError: If the unpack produced nothing, or a different row count
            than the payload implies. Both roll the whole landing back.
    """
    if summary.error is not None:
        raise ValueError(f"cannot land a failed year: {summary.error}")
    if summary.status_code is None or summary.payload_sha256 is None:
        raise ValueError("summary is missing status or payload hash")

    # An explicit transaction block on the caller's autocommit connection: the
    # commit happens here and nowhere else. Any exception inside the block - a
    # constraint violation, a failed cast, or the post-unpack assertion - rolls
    # back the raw upsert and every unpacked row together, so nothing is
    # half-landed and the connection stays reusable for the next year.
    with connection.transaction():
        connection.execute(
            """
            INSERT INTO ingest.events_by_year_raw (
                year,
                payload,
                http_status,
                source_final_count,
                source_original_count,
                received_count,
                payload_sha256,
                fetched_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (year) DO UPDATE SET
                payload = EXCLUDED.payload,
                http_status = EXCLUDED.http_status,
                source_final_count = EXCLUDED.source_final_count,
                source_original_count = EXCLUDED.source_original_count,
                received_count = EXCLUDED.received_count,
                payload_sha256 = EXCLUDED.payload_sha256,
                fetched_at = EXCLUDED.fetched_at,
                ingested_at = now();
            """,
            (
                summary.year,
                Jsonb(payload),
                summary.status_code,
                summary.source_final_count,
                summary.source_original_count,
                summary.received_count,
                summary.payload_sha256,
                summary.fetched_at,
            ),
        )
        unpacked = unpack_events_year(
            connection,
            summary.year,
            received_count=summary.received_count,
        )

    logger.info(
        "events_year_landed",
        extra={
            "year": summary.year,
            "received_count": summary.received_count,
            "unpacked": unpacked,
            "distinct_events": unpacked,
        },
    )

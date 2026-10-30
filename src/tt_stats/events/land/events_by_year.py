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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from psycopg.types.json import Jsonb

from tt_stats.core.db import get_connection
from tt_stats.core.logging_config import get_logger
from tt_stats.events.land.unpack import unpack_events_year

logger = get_logger(__name__)

if TYPE_CHECKING:
    from psycopg import Connection

    from tt_stats.events.collect.events_by_year import YearPayloadSummary

# What the unpack statement should have written, worked out from the payload
# that is now sitting in the raw table. Deliberately mirrors unpack_events.sql:
# the same JSON path, the same text-to-bigint cast, so a rename in one without
# the other shows up as a count mismatch rather than a silent difference.
#
#   items    one per element of payload -> 0 -> 'rows'
#   usable   elements whose EventId casts to bigint, i.e. rows the insert can use
#   distinct the rows the statement actually writes, after DISTINCT ON
#
# A cast failure here (non-numeric EventId) raises, which is the loud failure we
# want, not something to swallow.
_EXPECTATION_SQL = """
SELECT
    count(*) AS items,
    count((e.value ->> 'EventId')::bigint) AS usable,
    count(DISTINCT (e.value ->> 'EventId')::bigint) AS distinct_events
FROM ingest.events_by_year_raw AS r
CROSS JOIN LATERAL jsonb_array_elements(r.payload -> 0 -> 'rows') AS e
WHERE r.year = %s;
"""


@dataclass(frozen=True, slots=True)
class _Expectation:
    """Row counts the unpack should produce, derived from the raw payload."""

    items: int
    """Elements of ``payload -> 0 -> 'rows'``."""

    usable: int
    """Elements whose ``EventId`` casts to ``bigint``."""

    distinct_events: int
    """Distinct usable ids, which is what the statement inserts or updates."""


def land_events_by_year(
    summary: YearPayloadSummary,
    payload: Any,
) -> None:
    """Upsert one year's payload and unpack it in a single transaction.

    The unpack statement runs in the same transaction, after the insert, so the
    columns can always be rebuilt from the raw row that produced them. Its
    output is then counted and compared against the payload, so a rename or a
    dropped key aborts the transaction instead of committing a year that merely
    looks successful.

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

    # ``autocommit`` plus an explicit transaction block, rather than relying on
    # the connection context manager: the commit happens exactly here and
    # nowhere else. Any exception inside the block - a constraint violation, a
    # failed cast, or the post-unpack assertion below - rolls back the raw
    # upsert and every unpacked row together, so nothing is half-landed.
    with get_connection(autocommit=True) as connection, connection.transaction():
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
        unpacked = unpack_events_year(connection, summary.year)
        expected = _expected_unpack(connection, summary.year)
        # Raises before the transaction block exits, so psycopg rolls back
        # both the raw upsert and the unpacked rows.
        _assert_unpack_is_complete(
            summary.year,
            received_count=summary.received_count,
            unpacked=unpacked,
            expected=expected,
        )

    logger.info(
        "events_year_landed",
        extra={
            "year": summary.year,
            "received_count": summary.received_count,
            "unpacked": unpacked,
            "distinct_events": expected.distinct_events,
        },
    )


def _expected_unpack(connection: Connection[Any], year: int) -> _Expectation:
    """Return what the unpack statement should have produced for ``year``."""
    row = connection.execute(_EXPECTATION_SQL, (year,)).fetchone()
    if row is None:
        return _Expectation(items=0, usable=0, distinct_events=0)
    return _Expectation(
        items=int(row["items"]),
        usable=int(row["usable"]),
        distinct_events=int(row["distinct_events"]),
    )


def _assert_unpack_is_complete(
    year: int,
    *,
    received_count: int,
    unpacked: int,
    expected: _Expectation,
) -> None:
    """Fail the transaction unless the unpack wrote exactly what was expected.

    Three separate silent failures are caught here:

    1. ``'rows'`` renamed or missing upstream. ``jsonb_array_elements(NULL)``
       returns zero rows rather than raising, so the unpack writes nothing while
       the run still looks successful.
    2. The payload declares rows but none of them yield a usable ``EventId``,
       which is the same silence with a different cause.
    3. The unpack wrote a different number of rows than the payload contains.

    A year whose payload legitimately repeats an event is not a mismatch: the
    statement de-duplicates on ``event_id`` within the year, so the comparison
    is against distinct ids, not against ``received_count``. ``received_count``
    is still checked against the raw payload, because if it disagrees then the
    summary being landed is not describing the JSON that was stored.

    Raises:
        RuntimeError: On any of the conditions above. The caller aborts, and the
            landing transaction rolls back.
    """
    message = (
        f"Unpack validation failed for {year}: received {received_count} items "
        f"but unpacked {unpacked}. Upstream JSON path may have changed."
    )

    if received_count > 0 and (unpacked == 0 or expected.distinct_events == 0):
        logger.error(
            "events_unpack_empty",
            extra={
                "year": year,
                "received_count": received_count,
                "unpacked": unpacked,
                "json_items": expected.items,
                "distinct_events": expected.distinct_events,
            },
        )
        raise RuntimeError(message)

    if received_count != expected.items:
        raise RuntimeError(
            f"{message} Stored payload holds {expected.items} items but the "
            f"summary reports {received_count}."
        )

    if expected.usable != expected.items:
        raise RuntimeError(
            f"{message} {expected.items - expected.usable} of {expected.items} "
            f"items have no usable EventId."
        )

    if unpacked != expected.distinct_events:
        raise RuntimeError(
            f"{message} Expected {expected.distinct_events} distinct EventId "
            f"values from {expected.items} items."
        )

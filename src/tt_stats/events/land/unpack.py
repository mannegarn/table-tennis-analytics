"""Run the unpack statement that turns raw event payloads into columns.

The SQL lives in ``sql/unpack_events.sql`` so it can be read, reviewed and
edited as SQL rather than as a string literal inside Python. This module owns
loading it, executing it inside a caller's transaction, and the backfill entry
point used by ``tt-events-unpack``.

The statement file starts as a scaffold of comments. A comments-only file is
treated as "not written yet": loading returns ``None``, landing logs
``unpack_not_implemented`` and carries on, and the tests skip themselves. Write
the statement and everything switches on with no code change.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tt_stats.core.cli import add_dry_run_argument, report_unpack_run
from tt_stats.core.logging_config import configure_logging, get_logger
from tt_stats.core.sql_files import load_sql
from tt_stats.core.unpacking import unpack_each

if TYPE_CHECKING:
    from collections.abc import Sequence

    from psycopg import Connection

logger = get_logger(__name__)

UNPACK_SQL_PATH = Path(__file__).with_name("sql") / "unpack_events.sql"

# What the unpack statement should have written, worked out from the raw row.
# Deliberately mirrors unpack_events.sql: the same JSON path and text-to-bigint
# cast, so a rename or broken filter shows up as a count mismatch rather than
# silently unpacking nothing.
_EXPECTATION_SQL = """
SELECT
    r.received_count,
    count(e.value) AS items,
    count((e.value ->> 'EventId')::bigint) AS usable,
    count(DISTINCT (e.value ->> 'EventId')::bigint) AS distinct_events
FROM ingest.events_by_year_raw AS r
LEFT JOIN LATERAL jsonb_array_elements(r.payload -> 0 -> 'rows') AS e ON true
WHERE r.year = %s
GROUP BY r.year, r.received_count;
"""


@dataclass(frozen=True, slots=True)
class _Expectation:
    """Row counts the unpack should produce, derived from the raw payload."""

    received_count: int
    """Row count recorded on the raw payload row."""

    items: int
    """Elements of ``payload -> 0 -> 'rows'``."""

    usable: int
    """Elements whose ``EventId`` casts to ``bigint``."""

    distinct_events: int
    """Distinct usable ids, which is what the statement inserts or updates."""


def load_unpack_sql(path: Path | None = None) -> str | None:
    """Return the unpack statement, or ``None`` while the file is a scaffold.

    The path is resolved at call time rather than bound as a default argument, so
    tests can point ``UNPACK_SQL_PATH`` at a temporary SQL file.
    """
    return load_sql(path or UNPACK_SQL_PATH)


def unpack_events_year(
    connection: Connection[Any],
    year: int,
    *,
    received_count: int | None = None,
) -> int:
    """Unpack one year inside the caller's transaction and verify row counts.

    The statement upserts on ``(source_year, event_id)``. Re-running a year is
    therefore safe: row counts stay put, ``last_seen_at`` advances and
    ``first_seen_at`` is left as it was.

    Args:
        connection: An open connection. The caller owns the transaction, so the
            raw insert and this statement commit or roll back together.
        year: Calendar year to unpack, bound to the statement's ``%(year)s``
            placeholder.
        received_count: Optional count from the landing summary. When provided,
            the stored raw row is verified to match it.

    Returns:
        The number of rows the statement touched, or 0 when the statement has
        not been written yet.

    Raises:
        RuntimeError: If the unpack produced nothing for a non-empty payload, or
            a different row count than the stored payload implies.
    """
    statement = load_unpack_sql()
    if statement is None:
        logger.warning(
            "unpack_not_implemented",
            extra={"year": year, "sql": str(UNPACK_SQL_PATH)},
        )
        return 0

    # Bytes, not text: psycopg types a plain ``str`` as a
    # ``LiteralString``-only query, which static checkers reject for a value
    # read from disk. ``bytes`` sidesteps that entirely, as ``migrations.py``
    # already does.
    query = statement.encode("utf-8")
    # Named rather than positional: the statement mentions the year once now,
    # but a future clause that needs it again costs nothing to add.
    cursor = connection.execute(query, {"year": year})
    rows = cursor.rowcount or 0
    _verify_unpacked(
        connection,
        year,
        unpacked=rows,
        received_count=received_count,
    )
    logger.info("events_unpacked", extra={"year": year, "rows": rows})
    return rows


def _verify_unpacked(
    connection: Connection[Any],
    year: int,
    *,
    unpacked: int,
    received_count: int | None,
) -> None:
    """Fail the transaction unless the unpack wrote exactly what was expected."""
    row = connection.execute(_EXPECTATION_SQL, (year,)).fetchone()
    if row is None:
        if received_count is not None:
            raise RuntimeError(
                f"Unpack validation failed for {year}: raw payload row is missing."
            )
        if unpacked != 0:
            raise RuntimeError(
                f"Unpack validation failed for {year}: touched {unpacked} rows "
                "with no raw payload."
            )
        return

    expected = _Expectation(
        received_count=int(row["received_count"]),
        items=int(row["items"]),
        usable=int(row["usable"]),
        distinct_events=int(row["distinct_events"]),
    )
    expected_received = (
        expected.received_count if received_count is None else received_count
    )
    message = (
        f"Unpack validation failed for {year}: received {expected_received} items "
        f"but unpacked {unpacked}. Upstream JSON path may have changed."
    )

    if expected_received > 0 and (unpacked == 0 or expected.distinct_events == 0):
        logger.error(
            "events_unpack_empty",
            extra={
                "year": year,
                "received_count": expected_received,
                "unpacked": unpacked,
                "json_items": expected.items,
                "distinct_events": expected.distinct_events,
            },
        )
        raise RuntimeError(message)

    if expected_received != expected.items:
        raise RuntimeError(
            f"{message} Stored payload holds {expected.items} items but the "
            f"summary reports {expected_received}."
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


def landed_years(connection: Connection[Any]) -> list[int]:
    """Return every year present in the raw table, oldest first."""
    rows = connection.execute(
        "SELECT year FROM ingest.events_by_year_raw ORDER BY year;"
    ).fetchall()
    return [row["year"] for row in rows]


def unpack_years(
    years: Sequence[int] | None = None,
    *,
    dry_run: bool = False,
) -> list[tuple[int, int]]:
    """Unpack each year in its own transaction, returning ``(year, rows)``.

    One connection is reused across every year, and each year gets its own
    transaction so a failure on one year does not discard the others.

    With ``dry_run`` the statement still executes, so errors and row counts are
    real, but the transaction is rolled back before it commits. That is the
    quickest way to iterate on the SQL without cleaning up afterwards.
    """
    return unpack_each(
        years,
        landed_keys=landed_years,
        unpack=unpack_events_year,
        preview=_log_preview,
        dry_run=dry_run,
    )


def _log_preview(connection: Connection[Any], year: int, rows: int) -> None:
    """Log what a dry run would have written, before rolling it back."""
    row = connection.execute(
        "SELECT count(*) AS n FROM ingest.events_unpacked WHERE source_year = %s;",
        (year,),
    ).fetchone()
    total = row["n"] if row else 0
    logger.info(
        "unpack_preview",
        extra={"year": year, "rows_touched": rows, "rows_for_year": total},
    )
    if rows == 0:
        logger.warning(
            "unpack_preview_wrote_nothing",
            extra={"year": year, "hint": "check the year parameter and the SQL body"},
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-events-unpack``."""
    configure_logging()

    parser = argparse.ArgumentParser(
        description=(
            "Unpack landed WTT event calendars into ingest.events_unpacked. "
            "Without --years, every year present in the raw table is unpacked."
        )
    )
    parser.add_argument("--years", nargs="+", type=int, default=None)
    add_dry_run_argument(parser)
    args = parser.parse_args(argv)

    if load_unpack_sql() is None:
        logger.error(
            "unpack_not_implemented",
            extra={"sql": str(UNPACK_SQL_PATH)},
        )
        return 1

    results = unpack_years(args.years, dry_run=args.dry_run)
    return report_unpack_run(
        results,
        unit="year",
        found_label="events",
        dry_run=args.dry_run,
    )

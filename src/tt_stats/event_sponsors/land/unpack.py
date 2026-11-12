"""Run the unpack statement that turns raw sponsor payloads into sponsor rows.

The SQL lives in ``sql/unpack_event_sponsors.sql`` so it can be read, reviewed
and edited as SQL rather than as a string literal inside Python. This module
owns loading it, executing it inside a caller's transaction, and the backfill
entry point used by ``tt-event-sponsors-unpack``.
"""

from __future__ import annotations

import argparse
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

UNPACK_SQL_PATH = Path(__file__).with_name("sql") / "unpack_event_sponsors.sql"

_EXPECTATION_SQL = """
SELECT
    r.received_count,
    count(e.value) AS items,
    count(e.value ->> 'eventSponsorId') AS usable,
    count(DISTINCT (e.value ->> 'eventSponsorId')::bigint) AS sponsors
FROM ingest.event_sponsors_raw AS r
LEFT JOIN LATERAL jsonb_array_elements(r.payload) AS e ON true
WHERE r.event_id = %s
GROUP BY r.event_id, r.received_count;
"""


def load_unpack_sql(path: Path | None = None) -> str | None:
    """Return the unpack statement, or ``None`` while the file is a scaffold."""
    return load_sql(path or UNPACK_SQL_PATH)


def unpack_event(
    connection: Connection[Any],
    event_id: int,
    *,
    received_count: int | None = None,
) -> int:
    """Unpack one event's sponsor payload inside the caller's transaction and verify.

    Args:
        connection: An open connection. The caller owns the transaction, so the
            raw insert and this statement commit or roll back together.
        event_id: Event whose stored payload is unpacked.
        received_count: Optional count from the landing summary. When provided,
            the stored raw row is verified to match it.

    Returns:
        The number of rows the statement touched, or 0 when the statement has
        not been written yet.

    Raises:
        RuntimeError: If the unpack wrote a different number of rows than the
            stored payload implies.
    """
    statement = load_unpack_sql()
    if statement is None:
        logger.warning(
            "unpack_not_implemented",
            extra={"event_id": event_id, "sql": str(UNPACK_SQL_PATH)},
        )
        return 0

    query = statement.encode("utf-8")
    cursor = connection.execute(query, {"event_id": event_id})
    rows = cursor.rowcount or 0
    _verify_unpacked(
        connection,
        event_id=event_id,
        unpacked=rows,
        received_count=received_count,
    )
    logger.debug("event_sponsors_unpacked", extra={"event_id": event_id, "rows": rows})
    return rows


def _verify_unpacked(
    connection: Connection[Any],
    *,
    event_id: int,
    unpacked: int,
    received_count: int | None,
) -> None:
    """Raise unless the unpack wrote one row per distinct eventSponsorId."""
    row = connection.execute(_EXPECTATION_SQL, (event_id,)).fetchone()
    if row is None:
        if received_count is not None:
            raise RuntimeError(
                "Unpack validation failed: "
                f"raw payload row is missing for event {event_id}"
            )
        if unpacked != 0:
            raise RuntimeError(
                f"Unpack validation failed: event {event_id} touched {unpacked} rows "
                "with no raw payload"
            )
        return

    items = int(row["items"])
    usable = int(row["usable"])
    expected = int(row["sponsors"])
    expected_received = (
        int(row["received_count"]) if received_count is None else received_count
    )

    if expected_received != items:
        raise RuntimeError(
            "Unpack validation failed: "
            f"event {event_id} stored payload holds {items} items "
            f"but the summary reports {expected_received}"
        )
    if usable != items:
        raise RuntimeError(
            "Unpack validation failed: "
            f"event {event_id} has {items - usable} of {items} items "
            "without an eventSponsorId"
        )
    if unpacked != expected:
        raise RuntimeError(
            "Unpack validation failed: "
            f"event {event_id} holds {items} items with {expected} distinct "
            f"sponsor ids, but the unpack touched {unpacked} rows"
        )


def landed_event_ids(connection: Connection[Any]) -> list[int]:
    """Return every event present in the raw table, smallest id first."""
    rows = connection.execute(
        "SELECT event_id FROM ingest.event_sponsors_raw ORDER BY event_id;"
    ).fetchall()
    return [row["event_id"] for row in rows]


def unpack_events(
    event_ids: Sequence[int] | None = None,
    *,
    dry_run: bool = False,
) -> list[tuple[int, int]]:
    """Unpack each event in its own transaction, returning ``(event_id, rows)``."""
    return unpack_each(
        event_ids,
        landed_keys=landed_event_ids,
        unpack=unpack_event,
        preview=_log_preview,
        dry_run=dry_run,
    )


def _log_preview(connection: Connection[Any], event_id: int, rows: int) -> None:
    """Log what a dry run would have written, before rolling it back."""
    row = connection.execute(
        """
        SELECT
            (SELECT count(*) FROM ingest.event_sponsors_unpacked
             WHERE event_id = %s) AS unpacked,
            (SELECT received_count FROM ingest.event_sponsors_raw
             WHERE event_id = %s) AS received;
        """,
        (event_id, event_id),
    ).fetchone()
    unpacked = row["unpacked"] if row else 0
    received = row["received"] if row else 0
    logger.debug(
        "unpack_preview",
        extra={
            "event_id": event_id,
            "rows_touched": rows,
            "rows_for_event": unpacked,
            "received_count": received,
        },
    )
    if rows == 0 and received:
        logger.warning(
            "unpack_preview_wrote_nothing",
            extra={
                "event_id": event_id,
                "received_count": received,
                "hint": "the payload has sponsors but the statement wrote none",
            },
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-event-sponsors-unpack``."""
    configure_logging()

    parser = argparse.ArgumentParser(
        description=(
            "Unpack landed WTT sponsor payloads into ingest.event_sponsors_unpacked. "
            "Without --event-ids, every event present in the raw table is "
            "unpacked."
        )
    )
    parser.add_argument("--event-ids", nargs="+", type=int, default=None)
    add_dry_run_argument(parser)
    args = parser.parse_args(argv)

    if load_unpack_sql() is None:
        logger.error(
            "unpack_not_implemented",
            extra={"sql": str(UNPACK_SQL_PATH)},
        )
        return 1

    results = unpack_events(args.event_ids, dry_run=args.dry_run)
    return report_unpack_run(
        results,
        unit="event",
        found_label="sponsors",
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

"""Run the unpack statement that turns raw match lists into match rows.

The SQL lives in ``sql/unpack_match_entries.sql`` so it can be read, reviewed
and edited as SQL rather than as a string literal inside Python. This module
owns loading it, executing it inside a caller's transaction, and the backfill
entry point used by ``tt-matches-unpack``.

One event is one transaction. That mirrors how landing works: a broken event
cannot discard the work already done for the thousands of events before it, and
the 2307 events WTT answers with ``[]`` cost nothing but a no-op statement.

The statement file starts as a scaffold of comments. A comments-only file is
treated as "not written yet": loading returns ``None``, unpacking logs
``unpack_not_implemented`` and carries on, and the tests skip themselves.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tt_stats.core.db import get_connection
from tt_stats.core.logging_config import configure_logging, get_logger
from tt_stats.core.sql_files import load_sql

if TYPE_CHECKING:
    from collections.abc import Sequence

    from psycopg import Connection

logger = get_logger(__name__)

UNPACK_SQL_PATH = Path(__file__).with_name("sql") / "unpack_match_entries.sql"


def load_unpack_sql(path: Path | None = None) -> str | None:
    """Return the unpack statement, or ``None`` while the file is a scaffold.

    The path is resolved at call time rather than bound as a default argument, so
    a test can point the module at a scaffold and exercise the "not written yet"
    branch without touching the real statement.
    """
    return load_sql(path or UNPACK_SQL_PATH)


def unpack_event(connection: Connection[Any], event_id: int) -> int:
    """Unpack one event's match list inside the caller's transaction.

    The statement upserts on ``(event_id, document_code)``, so re-running an
    event is safe: row counts stay put, ``last_seen_at`` advances and
    ``first_seen_at`` is left as it was.

    Args:
        connection: An open connection. The caller owns the transaction, so the
            raw insert and this statement commit or roll back together.
        event_id: Event whose stored payload is unpacked, bound to the
            statement's ``%(event_id)s`` placeholder.

    Returns:
        The number of rows the statement touched, or 0 when the statement has
        not been written yet.
    """
    statement = load_unpack_sql()
    if statement is None:
        logger.warning(
            "unpack_not_implemented",
            extra={"event_id": event_id, "sql": str(UNPACK_SQL_PATH)},
        )
        return 0

    # Bytes, not text: psycopg types a plain ``str`` as a
    # ``LiteralString``-only query, which static checkers reject for a value
    # read from disk. ``bytes`` sidesteps that entirely, as ``migrations.py``
    # already does.
    query = statement.encode("utf-8")
    cursor = connection.execute(query, {"event_id": event_id})
    rows = cursor.rowcount or 0
    # One event per line would be 2838 lines per full backfill, so this is
    # debug; the run summary at the end of a backfill is the INFO line.
    logger.debug("match_entries_unpacked", extra={"event_id": event_id, "rows": rows})
    return rows


def landed_event_ids() -> list[int]:
    """Return every event present in the raw table, smallest id first."""
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT event_id FROM ingest.match_entries_payloads_raw ORDER BY event_id;"
        ).fetchall()
    return [row["event_id"] for row in rows]


def unpack_events(
    event_ids: Sequence[int] | None = None,
    *,
    dry_run: bool = False,
) -> list[tuple[int, int]]:
    """Unpack each event in its own transaction, returning ``(event_id, rows)``.

    With ``dry_run`` the statement still executes, so errors and row counts are
    real, but the transaction is rolled back before it commits. That is the
    quickest way to iterate on the SQL without cleaning up afterwards.

    One connection is reused for every event, and each event still gets its own
    transaction: connection setup per event would cost more than the unpack.
    """
    targets = list(event_ids) if event_ids else landed_event_ids()
    results: list[tuple[int, int]] = []

    with get_connection(autocommit=True) as connection:
        for event_id in targets:
            if dry_run:
                results.append((event_id, _preview_event(connection, event_id)))
                continue
            with connection.transaction():
                results.append((event_id, unpack_event(connection, event_id)))

    return results


class _PreviewRollback(Exception):
    """Forces a dry-run transaction to roll back instead of committing."""


def _preview_event(connection: Connection[Any], event_id: int) -> int:
    """Run one event's unpack in a transaction, report it, then discard it.

    The connection is in autocommit mode, where statements commit on their own,
    so the preview needs an explicit transaction block. Leaving that block by
    raising is what rolls it back: the counts below are read from the
    uncommitted rows, and the database keeps none of them.
    """
    rows = 0
    try:
        with connection.transaction():
            rows = unpack_event(connection, event_id)
            _log_preview(connection, event_id=event_id, rows=rows)
            raise _PreviewRollback
    except _PreviewRollback:
        pass
    return rows


def _log_preview(connection: Connection[Any], *, event_id: int, rows: int) -> None:
    """Log what a dry run would have written, before rolling it back."""
    row = connection.execute(
        """
        SELECT
            (SELECT count(*) FROM ingest.match_entries_unpacked
             WHERE event_id = %s) AS unpacked,
            (SELECT received_count FROM ingest.match_entries_payloads_raw
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
                "hint": "the payload has matches but the statement wrote none",
            },
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-matches-unpack``."""
    configure_logging()

    parser = argparse.ArgumentParser(
        description=(
            "Unpack landed WTT match lists into ingest.match_entries_unpacked. "
            "Without --event-ids, every event present in the raw table is "
            "unpacked."
        )
    )
    parser.add_argument("--event-ids", nargs="+", type=int, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the statement, report counts, then roll back.",
    )
    args = parser.parse_args(argv)

    if load_unpack_sql() is None:
        logger.error(
            "unpack_not_implemented",
            extra={"sql": str(UNPACK_SQL_PATH)},
        )
        return 1

    results = unpack_events(args.event_ids, dry_run=args.dry_run)
    events = len(results)
    rows = sum(touched for _, touched in results)
    empty = sum(1 for _, touched in results if touched == 0)
    logger.info(
        "unpack_complete",
        extra={
            "events": events,
            "rows": rows,
            "events_without_matches": empty,
            "dry_run": args.dry_run,
        },
    )
    # Deliberate stdout write: terminal logging sits at WARNING, so the log
    # line above never reaches the console. This is the CLI's user-facing
    # confirmation, not application logging.
    print(  # noqa: T201
        f"Unpack complete: {events} events, {rows} rows "
        f"({empty} without matches, dry_run={args.dry_run})"
    )
    return 0

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
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tt_stats.core.db import get_connection
from tt_stats.core.logging_config import configure_logging, get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from psycopg import Connection

logger = get_logger(__name__)

UNPACK_SQL_PATH = Path(__file__).with_name("sql") / "unpack_events.sql"

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"--[^\n]*")


def executable_sql(text: str) -> str:
    """Return ``text`` with SQL comments stripped.

    Used to tell a scaffold from a real statement. Deliberately dumb: it only
    needs to answer "is there anything here besides comments".
    """
    without_blocks = _BLOCK_COMMENT.sub(" ", text)
    return _LINE_COMMENT.sub(" ", without_blocks).strip()


def load_unpack_sql(path: Path = UNPACK_SQL_PATH) -> str | None:
    """Return the unpack statement, or ``None`` while the file is a scaffold."""
    text = path.read_text(encoding="utf-8")
    if not executable_sql(text):
        return None
    return text


def unpack_events_year(connection: Connection[Any], year: int) -> int:
    """Unpack one year inside the caller's transaction.

    The statement upserts on ``(source_year, event_id)``. Re-running a year is
    therefore safe: row counts stay put, ``last_seen_at`` advances and
    ``first_seen_at`` is left as it was.

    Args:
        connection: An open connection. The caller owns the transaction, so the
            raw insert and this statement commit or roll back together.
        year: Calendar year to unpack, bound to the statement's ``%(year)s``
            placeholder.

    Returns:
        The number of rows the statement touched, or 0 when the statement has
        not been written yet.
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
    logger.info("events_unpacked", extra={"year": year, "rows": rows})
    return rows


def landed_years() -> list[int]:
    """Return every year present in the raw table, oldest first."""
    with get_connection() as connection:
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

    One transaction per year keeps a failing year from discarding the others,
    which matches how landing already works.

    With ``dry_run`` the statement still executes, so errors and row counts are
    real, but the transaction is rolled back before it commits. That is the
    quickest way to iterate on the SQL without cleaning up afterwards.
    """
    targets = list(years) if years else landed_years()
    results: list[tuple[int, int]] = []

    for year in targets:
        with get_connection() as connection:
            rows = unpack_events_year(connection, year)
            if dry_run:
                _log_preview(connection, year=year, rows=rows)
                connection.rollback()
        results.append((year, rows))

    return results


def _log_preview(connection: Connection[Any], *, year: int, rows: int) -> None:
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

    results = unpack_years(args.years, dry_run=args.dry_run)
    years = len(results)
    rows = sum(touched for _, touched in results)
    logger.info(
        "unpack_complete",
        extra={"years": years, "rows": rows, "dry_run": args.dry_run},
    )
    # Deliberate stdout write: terminal logging sits at WARNING, so the log
    # line above never reaches the console. This is the CLI's user-facing
    # confirmation, not application logging.
    print(  # noqa: T201
        f"Unpack complete: {years} years, {rows} rows (dry_run={args.dry_run})"
    )
    return 0

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
from typing import TYPE_CHECKING, Any, cast

from tt_stats.core.db import get_connection
from tt_stats.core.logging_config import configure_logging, get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import LiteralString

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

    Args:
        connection: An open connection. The caller owns the transaction, so the
            raw insert and this statement commit or roll back together.
        year: Calendar year to unpack, passed as the single statement parameter.

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

    # Repo-controlled SQL read from disk, never user input, so asserting what
    # psycopg's LiteralString guard asks for is accurate. mypy considers plain
    # str already compatible and calls the cast redundant; ty does not.
    query = cast("LiteralString", statement)  # type: ignore[redundant-cast]
    cursor = connection.execute(query, (year,))
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


def unpack_years(years: Sequence[int] | None = None) -> list[tuple[int, int]]:
    """Unpack each year in its own transaction, returning ``(year, rows)``.

    One transaction per year keeps a failing year from discarding the others,
    which matches how landing already works.
    """
    targets = list(years) if years else landed_years()
    results: list[tuple[int, int]] = []

    for year in targets:
        with get_connection() as connection:
            rows = unpack_events_year(connection, year)
        results.append((year, rows))

    return results


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
    args = parser.parse_args(argv)

    if load_unpack_sql() is None:
        logger.error(
            "unpack_not_implemented",
            extra={"sql": str(UNPACK_SQL_PATH)},
        )
        return 1

    results = unpack_years(args.years)
    logger.info(
        "unpack_complete",
        extra={
            "years": len(results),
            "rows": sum(rows for _, rows in results),
        },
    )
    return 0

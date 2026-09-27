"""PostgreSQL connection helpers built on psycopg 3."""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.rows import RowFactory, dict_row

from tt_stats.core.config import get_settings
from tt_stats.core.logging_config import configure_logging, get_logger

logger = get_logger(__name__)


@contextmanager
def get_connection(
    *,
    autocommit: bool = False,
    row_factory: RowFactory[Any] | None = dict_row,
) -> Generator[Connection[Any]]:
    """Yield a PostgreSQL connection with transaction safety.

    psycopg's connection context manager commits on a clean exit and rolls
    back when an exception propagates, so callers get correct transaction
    behaviour without manual ``commit`` or ``rollback`` calls.

    Args:
        autocommit: When True, statements commit immediately. Useful for
            DDL and for simple reads.
        row_factory: How rows are returned. Defaults to ``dict_row`` so
            callers can index by column name.
    """
    settings = get_settings()
    with psycopg.connect(
        settings.dsn,
        autocommit=autocommit,
        row_factory=row_factory,
    ) as connection:
        yield connection


def check_connection() -> bool:
    """Return True if the database is reachable, logging what it finds."""
    try:
        with get_connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT version() AS version;")
            row = cursor.fetchone()
        version = row["version"] if row else "unknown"
        logger.info("Connected to PostgreSQL: %s", version)
        return True
    except Exception as error:
        logger.error("Connection failed: %s", error)
        return False


def main() -> int:
    """Console entry point used by the ``tt-db-check`` script.

    This is an entry point, so it configures logging before doing any work.
    ``check_connection`` itself only logs, and assumes someone else set that up.
    """
    configure_logging()
    return 0 if check_connection() else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""PostgreSQL connection helpers built on psycopg 3."""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.rows import RowFactory, dict_row

from tt_stats.core.config import Settings, get_settings
from tt_stats.core.logging_config import configure_logging, get_logger

logger = get_logger(__name__)


@contextmanager
def get_connection(
    *,
    settings: Settings | None = None,
    autocommit: bool = True,
    row_factory: RowFactory[Any] | None = dict_row,
) -> Generator[Connection[Any]]:
    """Yield a PostgreSQL connection configured for explicit transactions.

    ``autocommit`` defaults to ``True`` so read queries do not leave the session
    idle in an open transaction, and each ``with connection.transaction():``
    block commits or rolls back its own unit of work independently across a
    persistent connection.

    Args:
        settings: Configuration to connect with. Defaults to the cached
            process settings; passing one keeps callers testable without
            patching the environment.
        autocommit: When True (the default), statements outside an explicit
            ``with connection.transaction():`` block commit immediately.
        row_factory: How rows are returned. Defaults to ``dict_row`` so
            callers can index by column name.
    """
    resolved = settings or get_settings()
    with psycopg.connect(
        resolved.dsn,
        autocommit=autocommit,
        row_factory=row_factory,
    ) as connection:
        yield connection


def check_connection() -> bool:
    """Return True if the database is reachable, logging what it finds."""
    try:
        with get_connection() as connection:
            row = connection.execute("SELECT version() AS version;").fetchone()
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
    if not check_connection():
        return 1
    # Deliberate stdout write: terminal logging sits at WARNING, so the log
    # line inside ``check_connection`` never reaches the console. This is the
    # CLI's user-facing confirmation, not application logging.
    print("Database connection OK")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Apply numbered SQL migrations from ``db/migrations``.

Migrations are ingestion-owned DDL. They are not dbt models: dbt reads the
resulting tables through ``sources.yml``.

The directory is resolved in this order: an explicit argument, then
``MIGRATIONS_DIR`` from the environment, then the repository's own
``db/migrations``. The default keeps a plain checkout working with no
configuration, while the environment variable covers containers and wheel
installs where the repository layout is absent.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from psycopg import sql

from tt_stats.core.config import get_settings, project_root
from tt_stats.core.db import get_connection
from tt_stats.core.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

DEFAULT_MIGRATIONS_DIR = project_root() / "db" / "migrations"


def resolve_migrations_dir(directory: Path | None = None) -> Path:
    """Return the migration directory to use.

    Args:
        directory: Explicit override, normally from a command line or a test.

    Raises:
        ValidationError: If no directory is given and Settings cannot be built,
            for example because the database password is unset.
    """
    if directory is not None:
        return directory

    configured = get_settings().migrations_dir
    return configured or DEFAULT_MIGRATIONS_DIR


# ``Final`` keeps this a literal type, so ``sql.SQL`` accepts it without a cast.
SCHEMA_VERSION_DDL: Final = """
CREATE SCHEMA IF NOT EXISTS ingest;
CREATE TABLE IF NOT EXISTS ingest.schema_version (
    version text PRIMARY KEY,
    checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);
"""


@dataclass(frozen=True, slots=True)
class Migration:
    """One numbered SQL file and its content fingerprint."""

    version: str
    path: Path
    checksum: str


def discover_migrations(
    directory: Path | None = None,
) -> list[Migration]:
    """Return migrations in filename order, with a SHA-256 each."""
    directory = resolve_migrations_dir(directory)
    if not directory.exists():
        raise FileNotFoundError(f"Migrations directory not found: {directory}")

    return [
        Migration(
            version=path.stem,
            path=path,
            checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(directory.glob("*.sql"))
    ]


def apply_migrations(directory: Path | None = None) -> list[str]:
    """Apply every migration not yet recorded, returning their versions.

    Each migration runs in its own transaction. A checksum change on an
    already-applied file is an error, because that would make the database
    history disagree with the file on disk.
    """
    migrations = discover_migrations(directory)
    applied: list[str] = []

    with get_connection(autocommit=True) as connection:
        connection.execute(sql.SQL(SCHEMA_VERSION_DDL))
        rows = connection.execute(
            "SELECT version, checksum FROM ingest.schema_version;"
        ).fetchall()
        known = {row["version"]: row["checksum"] for row in rows}

        for migration in migrations:
            existing = known.get(migration.version)
            if existing is not None:
                if existing != migration.checksum:
                    raise RuntimeError(
                        f"Migration {migration.version} changed after it was applied."
                    )
                continue

            with connection.transaction():
                # Bytes, not text: psycopg types a plain ``str`` as a
                # ``LiteralString``-only query, which static checkers reject for
                # a value read from disk. ``bytes`` sidesteps that entirely and
                # is passed through to the server unmodified.
                migration_sql = migration.path.read_bytes()
                with connection.cursor() as cur:
                    cur.execute(migration_sql)
                    cur.execute(
                        "INSERT INTO ingest.schema_version (version, checksum) "
                        "VALUES (%s, %s);",
                        (migration.version, migration.checksum),
                    )

            applied.append(migration.version)
            logger.info(
                "migration_applied",
                extra={"version": migration.version},
            )

    return applied


def main() -> int:
    """Console entry point used by ``tt-db-migrate``."""
    configure_logging()
    try:
        applied = apply_migrations()
    except Exception as error:
        logger.error("migrations_failed", extra={"error": str(error)})
        return 1

    logger.info("migrations_complete", extra={"applied": len(applied)})
    # Deliberate stdout write: terminal logging sits at WARNING, so the log
    # line above never reaches the console. This is the CLI's user-facing
    # confirmation, not application logging.
    print(f"Migrations complete: {len(applied)} applied")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Integration-suite fixtures: everything here runs against ``tt_stats_test``.

Both fixtures are autouse on purpose. A test cannot forget to point at the test
database, and cannot leave fixture rows behind for the next test to trip over.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest

from tt_stats.core.config import Settings
from tt_stats.core.db import get_connection
from tt_stats.core.migrations import apply_migrations

# The only tables tests write to. Truncating them removes fixture data without
# the cost of dropping and recreating a schema.
INGEST_TABLES = (
    "ingest.events_unpacked",
    "ingest.events_by_year_raw",
    "ingest.ingest_runs",
)

TRUNCATE_SQL = f"TRUNCATE {', '.join(INGEST_TABLES)} RESTART IDENTITY CASCADE;"


@pytest.fixture(scope="session", autouse=True)
def _test_database(test_settings: Settings) -> None:
    """Skip the suite when Postgres is down, else bring the schema up to date.

    Applying migrations here means the test database has the same schema as the
    application database, built from the same tracked files.
    """
    try:
        with psycopg.connect(test_settings.test_dsn):
            pass
    except psycopg.OperationalError as error:
        pytest.skip(f"PostgreSQL not available: {error}")

    apply_migrations()


@pytest.fixture(autouse=True)
def _clean_ingest_tables(
    test_settings: Settings,
    _test_database: None,
) -> Iterator[None]:
    """Fast-truncate the ingest tables around each test.

    ``RESTART IDENTITY`` keeps generated keys stable, and ``CASCADE`` clears
    dependants so adding an ingest table later needs no change here.
    """
    _truncate(test_settings)
    yield
    _truncate(test_settings)


def _truncate(settings: Settings) -> None:
    with get_connection(settings=settings, autocommit=True) as connection:
        connection.execute(TRUNCATE_SQL)

"""Integration test: the local warehouse is reachable and usable.

Requires the PostgreSQL container from docker compose. When the database is
not reachable the tests skip rather than fail, so the default test run stays
green on a machine without Docker running.

Run explicitly with:
    make test-integration
"""

from collections.abc import Iterator

import psycopg
import pytest
from pydantic import ValidationError

from tt_stats.core.config import get_settings

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def connection() -> Iterator[psycopg.Connection]:
    """Yield a live connection, or skip the module if none is available."""
    try:
        settings = get_settings()
    except ValidationError as error:
        pytest.skip(f"Database settings are incomplete: {error}")

    try:
        conn = psycopg.connect(settings.dsn)
    except psycopg.OperationalError as error:
        pytest.skip(f"PostgreSQL not available: {error}")
    yield conn
    conn.close()


def test_select_one(connection: psycopg.Connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 AS one;")
        assert cursor.fetchone() == (1,)


def test_public_schema_exists(connection: psycopg.Connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1
            FROM information_schema.schemata
            WHERE schema_name = 'public';
            """
        )
        assert cursor.fetchone() == (1,)

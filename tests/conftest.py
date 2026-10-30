"""Shared pytest fixtures.

Integration tests are pinned to a dedicated ``tt_stats_test`` database so that
fixture rows can be truncated without ever touching scraped data. The database
is created by ``db/init/000_create_test_db.sql`` on a fresh container; the
header of that file documents the one-line ``psql`` command for a container
that already exists.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg import Connection
from psycopg.conninfo import conninfo_to_dict
from pydantic import ValidationError

from tt_stats.core.config import Settings, get_settings
from tt_stats.core.db import get_connection


def _database_name(dsn: str) -> str | None:
    """Return the ``dbname`` of a connection string, if it parses."""
    try:
        value = conninfo_to_dict(dsn).get("dbname")
    except psycopg.Error:
        return None
    return None if value is None else str(value)


@pytest.fixture(scope="session")
def test_settings() -> Iterator[Settings]:
    """Settings pinned to the isolated integration-test database.

    The pin is applied to ``DATABASE_URL`` rather than by patching
    ``get_connection``, because application code opens its own connections
    through the cached settings. Pinning the environment keeps those calls on
    the test database too, and survives a test that clears the settings cache.

    The fixture fails rather than skips when the resolved test database is the
    same one the application uses: silently testing against ``tt_stats`` is the
    failure mode this whole arrangement exists to prevent.
    """
    try:
        settings = Settings()  # pyright: ignore[reportCallIssue]
    except ValidationError as error:
        pytest.skip(f"Database settings are incomplete: {error}")

    application_db = _database_name(settings.dsn)
    test_db = _database_name(settings.test_dsn)
    if test_db is not None and test_db == application_db:
        pytest.fail(
            "Refusing to run tests against the application database "
            f"{test_db!r}; set TEST_DATABASE_NAME or TEST_DATABASE_URL.",
        )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("DATABASE_URL", settings.test_dsn)
    get_settings.cache_clear()
    try:
        yield Settings()  # pyright: ignore[reportCallIssue]
    finally:
        get_settings.cache_clear()
        monkeypatch.undo()
        get_settings.cache_clear()


@pytest.fixture
def db_connection(test_settings: Settings) -> Iterator[Connection[Any]]:
    """Yield a connection bound strictly to the test database.

    Transactions stay under the caller's control, which is how the application
    code behaves: nothing here wraps a test in a synthetic outer transaction.
    """
    with get_connection(settings=test_settings) as connection:
        yield connection

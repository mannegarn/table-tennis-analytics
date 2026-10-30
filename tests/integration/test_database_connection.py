"""Integration test: the test warehouse is reachable and usable.

Runs against the isolated ``tt_stats_test`` database. The fixtures in
``tests/integration/conftest.py`` skip the suite when PostgreSQL is not
reachable, so the default test run stays green on a machine without Docker.

Run explicitly with:
    make test-integration
"""

from typing import Any

import pytest
from psycopg import Connection

pytestmark = pytest.mark.integration


def test_select_one(db_connection: Connection[Any]) -> None:
    row = db_connection.execute("SELECT 1 AS one;").fetchone()
    assert row == {"one": 1}


def test_public_schema_exists(db_connection: Connection[Any]) -> None:
    row = db_connection.execute(
        """
        SELECT 1 AS one
        FROM information_schema.schemata
        WHERE schema_name = 'public';
        """
    ).fetchone()
    assert row == {"one": 1}

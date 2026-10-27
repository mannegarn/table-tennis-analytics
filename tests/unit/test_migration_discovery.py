"""Unit tests for migration discovery. No database involved."""

import hashlib
from pathlib import Path

import pytest

from tt_stats.core.migrations import discover_migrations


def test_discover_migrations_sorts_and_hashes(tmp_path: Path) -> None:
    second = tmp_path / "002_second.sql"
    first = tmp_path / "001_first.sql"
    second.write_text("select 2;\n", encoding="utf-8")
    first.write_text("select 1;\n", encoding="utf-8")

    migrations = discover_migrations(tmp_path)

    assert [migration.version for migration in migrations] == [
        "001_first",
        "002_second",
    ]
    assert migrations[0].checksum == hashlib.sha256(b"select 1;\n").hexdigest()
    assert all(len(migration.checksum) == 64 for migration in migrations)


def test_discover_migrations_requires_a_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        discover_migrations(tmp_path / "missing")

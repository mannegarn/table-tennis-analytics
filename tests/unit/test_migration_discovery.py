"""Unit tests for migration discovery. No database involved."""

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from tt_stats.core.config import get_settings
from tt_stats.core.migrations import (
    DEFAULT_MIGRATIONS_DIR,
    discover_migrations,
    resolve_migrations_dir,
)


@pytest.fixture
def _fresh_settings() -> Iterator[None]:
    """Clear the Settings cache so a patched environment is actually read."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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


@pytest.mark.usefixtures("_fresh_settings")
def test_default_directory_is_the_repository_migrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "test")
    monkeypatch.delenv("MIGRATIONS_DIR", raising=False)

    assert resolve_migrations_dir() == DEFAULT_MIGRATIONS_DIR
    assert "001_events_raw" in {m.version for m in discover_migrations()}


@pytest.mark.usefixtures("_fresh_settings")
def test_environment_variable_overrides_the_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "009_env.sql").write_text("select 9;\n", encoding="utf-8")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test")
    monkeypatch.setenv("MIGRATIONS_DIR", str(tmp_path))

    assert [m.version for m in discover_migrations()] == ["009_env"]


@pytest.mark.usefixtures("_fresh_settings")
def test_explicit_directory_beats_the_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_dir = tmp_path / "env"
    arg_dir = tmp_path / "arg"
    env_dir.mkdir()
    arg_dir.mkdir()
    (env_dir / "002_env.sql").write_text("select 2;\n", encoding="utf-8")
    (arg_dir / "001_arg.sql").write_text("select 1;\n", encoding="utf-8")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test")
    monkeypatch.setenv("MIGRATIONS_DIR", str(env_dir))

    assert resolve_migrations_dir(arg_dir) == arg_dir
    assert [m.version for m in discover_migrations(arg_dir)] == ["001_arg"]

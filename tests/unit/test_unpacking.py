"""Unit tests for the per-key transaction and dry-run loop in ``core.unpacking``."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import psycopg

from tt_stats.core import unpacking
from tt_stats.core.unpacking import unpack_each

if TYPE_CHECKING:
    from collections.abc import Iterator

    import pytest


class _FakeConnection:
    """Minimal stand-in for a psycopg autocommit connection."""

    def __init__(self) -> None:
        self.committed: list[int] = []
        self.rolled_back: list[int] = []
        self._active_key: int | None = None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
        except psycopg.Rollback:
            if self._active_key is not None:
                self.rolled_back.append(self._active_key)
        else:
            if self._active_key is not None:
                self.committed.append(self._active_key)


def test_unpack_each_commits_each_key_in_its_own_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeConnection()

    @contextmanager
    def fake_get_connection(*, autocommit: bool = True) -> Iterator[Any]:
        assert autocommit is True
        yield fake

    monkeypatch.setattr(unpacking, "get_connection", fake_get_connection)

    def unpack_one(_conn: Any, key: int) -> int:
        fake._active_key = key
        return key * 10

    results = unpack_each(
        None,
        landed_keys=lambda _conn: [1, 2],
        unpack=unpack_one,
        preview=lambda _conn, _key, _rows: None,
        dry_run=False,
    )

    assert results == [(1, 10), (2, 20)]
    assert fake.committed == [1, 2]
    assert fake.rolled_back == []


def test_unpack_each_dry_run_previews_and_rolls_back_every_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeConnection()
    previewed: list[tuple[int, int]] = []

    @contextmanager
    def fake_get_connection(*, autocommit: bool = True) -> Iterator[Any]:
        assert autocommit is True
        yield fake

    monkeypatch.setattr(unpacking, "get_connection", fake_get_connection)

    def unpack_one(_conn: Any, key: int) -> int:
        fake._active_key = key
        return 5

    results = unpack_each(
        [10, 20],
        landed_keys=lambda _conn: [1, 2],
        unpack=unpack_one,
        preview=lambda _conn, key, rows: previewed.append((key, rows)),
        dry_run=True,
    )

    assert results == [(10, 5), (20, 5)]
    assert previewed == [(10, 5), (20, 5)]
    assert fake.committed == []
    assert fake.rolled_back == [10, 20]

"""Unit tests for the CI database fail-instead-of-skip guard."""

from __future__ import annotations

import pytest
from _pytest.outcomes import Failed, Skipped
from tests.conftest import skip_or_fail_db


def test_skip_or_fail_db_fails_when_ci_is_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CI", "true")
    with pytest.raises(Failed, match="db down"):
        skip_or_fail_db("db down")


def test_skip_or_fail_db_skips_when_not_in_ci(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(Skipped, match="db down"):
        skip_or_fail_db("db down")

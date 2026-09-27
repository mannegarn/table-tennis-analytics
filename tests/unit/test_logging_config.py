"""Unit tests for the structured logging setup.

No database involved, and no network. The Settings-driven test exercises the
configuration wiring, everything else passes explicit options.
"""

import json
import logging
from collections.abc import Iterator

import pytest

from tt_stats.core.config import get_settings
from tt_stats.core.logging_config import (
    ConsoleFormatter,
    JsonFormatter,
    configure_logging,
    get_logger,
)


@pytest.fixture(autouse=True)
def _restore_root_logger() -> Iterator[None]:
    """Snapshot and restore the root logger so tests cannot leak into each other."""
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level

    yield

    for handler in root.handlers[:]:
        root.removeHandler(handler)
    for handler in original_handlers:
        root.addHandler(handler)
    root.setLevel(original_level)


def _make_record(**extras: object) -> logging.LogRecord:
    """Build a LogRecord with arbitrary extras attached."""
    record = logging.LogRecord(
        name="tt_stats.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="scraped_year",
        args=(),
        exc_info=None,
    )
    for key, value in extras.items():
        setattr(record, key, value)
    return record


def test_configure_logging_is_idempotent() -> None:
    configure_logging(level="INFO", json_output=False)
    configure_logging(level="INFO", json_output=False)

    assert len(logging.getLogger().handlers) == 1


def test_configure_logging_sets_level() -> None:
    configure_logging(level="debug", json_output=False)

    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_selects_the_formatter() -> None:
    configure_logging(level="INFO", json_output=False)
    assert isinstance(logging.getLogger().handlers[0].formatter, ConsoleFormatter)

    configure_logging(level="INFO", json_output=True)
    assert isinstance(logging.getLogger().handlers[0].formatter, JsonFormatter)


def test_get_logger_returns_a_named_logger() -> None:
    logger = get_logger("tt_stats.some.module")

    assert isinstance(logger, logging.Logger)
    assert logger.name == "tt_stats.some.module"


def test_json_formatter_emits_extras_as_top_level_fields() -> None:
    payload = json.loads(JsonFormatter().format(_make_record(year=2021, rows=189)))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "tt_stats.test"
    assert payload["msg"] == "scraped_year"
    assert payload["year"] == 2021
    assert payload["rows"] == 189
    assert "time" in payload


def test_json_formatter_handles_unserialisable_extras() -> None:
    payload = json.loads(JsonFormatter().format(_make_record(when=object())))

    assert isinstance(payload["when"], str)


def test_console_formatter_appends_extras() -> None:
    rendered = ConsoleFormatter("%(message)s").format(_make_record(year=2021))

    assert "scraped_year" in rendered
    assert "year=2021" in rendered


def test_configure_logging_reads_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LOG_JSON", "true")
    get_settings.cache_clear()

    try:
        configure_logging()
    finally:
        get_settings.cache_clear()

    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert isinstance(root.handlers[0].formatter, JsonFormatter)

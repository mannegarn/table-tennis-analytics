"""Unit tests for the structured logging setup.

No database involved, and no network. The Settings-driven test exercises the
configuration wiring, everything else passes explicit options.
"""

import json
import logging
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from tt_stats.core.config import get_settings
from tt_stats.core.logging_config import (
    ConsoleFormatter,
    JsonFormatter,
    configure_logging,
    get_logger,
    progress_owns_console,
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


def _flush_handlers() -> None:
    """Force buffered file handlers to disk before asserting on their contents."""
    for handler in logging.getLogger().handlers:
        handler.flush()


def _configure_with_file(log_file: Path, *, json_output: bool = True) -> None:
    """Configure logging with a file handler at a test-owned path."""
    configure_logging(
        level="INFO",
        json_output=json_output,
        log_to_file=True,
        log_file=log_file,
    )


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
    configure_logging(level="INFO", json_output=False, log_to_file=False)
    configure_logging(level="INFO", json_output=False, log_to_file=False)

    assert len(logging.getLogger().handlers) == 1


def test_configure_logging_adds_one_file_handler(tmp_path: Path) -> None:
    log_file = tmp_path / "tt_stats.log"

    _configure_with_file(log_file)
    _configure_with_file(log_file)

    root = logging.getLogger()
    assert len(root.handlers) == 2
    assert any(isinstance(handler, RotatingFileHandler) for handler in root.handlers)


def test_configure_logging_creates_the_log_directory(tmp_path: Path) -> None:
    log_file = tmp_path / "nested" / "deeper" / "tt_stats.log"

    _configure_with_file(log_file, json_output=False)

    assert log_file.parent.is_dir()


def test_file_handler_records_what_the_console_prints(tmp_path: Path) -> None:
    log_file = tmp_path / "tt_stats.log"
    _configure_with_file(log_file, json_output=False)

    get_logger("tt_stats.test").info("scraped_year", extra={"year": 2021})
    _flush_handlers()

    written = log_file.read_text(encoding="utf-8")
    assert "scraped_year" in written
    assert "year=2021" in written


def test_configure_logging_sets_level() -> None:
    configure_logging(level="debug", json_output=False, log_to_file=False)

    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_selects_the_formatter() -> None:
    configure_logging(level="INFO", json_output=False, log_to_file=False)
    assert isinstance(logging.getLogger().handlers[0].formatter, ConsoleFormatter)

    configure_logging(level="INFO", json_output=True, log_to_file=False)
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
    monkeypatch.setenv("LOG_TO_FILE", "false")
    get_settings.cache_clear()

    try:
        configure_logging()
    finally:
        get_settings.cache_clear()

    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
    assert len(root.handlers) == 1


def test_progress_owns_console_keeps_info_out_of_the_console(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    log_file = tmp_path / "tt_stats.log"
    _configure_with_file(log_file, json_output=False)

    with progress_owns_console():
        get_logger("tt_stats.events.land").info(
            "events_year_landed",
            extra={"year": 2026},
        )
    _flush_handlers()

    assert "events_year_landed" not in capsys.readouterr().out
    assert "events_year_landed" in log_file.read_text(encoding="utf-8")


def test_progress_owns_console_still_prints_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _configure_with_file(tmp_path / "tt_stats.log", json_output=False)

    with progress_owns_console():
        get_logger("tt_stats.events.land").error("landing_exploded")

    assert "landing_exploded" in capsys.readouterr().out


def test_console_resumes_when_the_bar_finishes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _configure_with_file(tmp_path / "tt_stats.log", json_output=False)
    logger = get_logger("tt_stats.events.land")

    with progress_owns_console():
        pass
    logger.info("after_the_bar")

    assert "after_the_bar" in capsys.readouterr().out

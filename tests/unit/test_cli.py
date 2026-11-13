"""Unit tests for the shared collector and unpacker CLI helpers."""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from tt_stats.core.cli import (
    add_dry_run_argument,
    add_queue_arguments,
    chunk_count,
    log_chunk_progress,
    progress_message,
    quiet_http_logs,
    report_collect_run,
    report_unpack_run,
    run_with_progress,
    validate_queue_arguments,
)
from tt_stats.core.config import Settings


@dataclass(frozen=True, slots=True)
class _Summary:
    key: int
    received_count: int
    error: str | None = None
    reason: str | None = None


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "postgres_password": "test",
        "log_json": True,
    }
    values.update(overrides)
    return Settings(**values)


def test_chunk_count_rounds_up() -> None:
    assert chunk_count(1, 50) == 1
    assert chunk_count(50, 50) == 1
    assert chunk_count(51, 50) == 2
    assert chunk_count(0, 50) == 0


def test_progress_message_describes_ok_empty_and_failed_items() -> None:
    assert (
        progress_message(
            _Summary(key=3098, received_count=306, reason="live"),
            key="event=3098",
            count_label="matches",
        )
        == "live event=3098 | matches=306 | ok"
    )
    assert (
        progress_message(
            _Summary(key=1934, received_count=0),
            key="year=1934",
            count_label="received",
        )
        == "year=1934 | received=0 | empty"
    )
    assert (
        progress_message(
            _Summary(key=2024, received_count=0, error="timeout"),
            key="year=2024",
            count_label="received",
        )
        == "year=2024 | received=0 | failed"
    )


def test_queue_and_dry_run_arguments_parse_and_validate() -> None:
    parser = argparse.ArgumentParser()
    add_queue_arguments(parser, unit="event")
    add_dry_run_argument(parser)

    args = parser.parse_args(["--limit", "5", "--chunk-size", "25", "--dry-run"])
    validate_queue_arguments(parser, args)

    assert args.limit == 5
    assert args.chunk_size == 25
    assert args.dry_run is True

    with pytest.raises(SystemExit) as caught:
        validate_queue_arguments(parser, parser.parse_args(["--limit", "0"]))
    assert caught.value.code == 2


def test_quiet_http_logs_silences_httpx_unless_debug() -> None:
    httpx_logger = logging.getLogger("httpx")
    original = httpx_logger.level
    try:
        httpx_logger.setLevel(logging.INFO)
        quiet_http_logs(_settings(log_level="INFO"))
        assert httpx_logger.level == logging.WARNING

        httpx_logger.setLevel(logging.INFO)
        quiet_http_logs(_settings(log_level="DEBUG"))
        assert httpx_logger.level == logging.INFO
    finally:
        httpx_logger.setLevel(original)


def test_run_with_progress_drives_async_run_and_returns_summaries() -> None:
    items = [_Summary(key=1, received_count=3), _Summary(key=2, received_count=0)]

    async def _collect(advance: Any) -> list[_Summary]:
        for item in items:
            advance(item)
        return items

    results = run_with_progress(
        len(items),
        description="Collecting test",
        unit="item",
        chunk_size=10,
        settings=_settings(),
        message=lambda s: progress_message(s, key=f"id={s.key}", count_label="rows"),
        run=_collect,
    )

    assert results == items


def test_log_chunk_progress_and_report_collect_run_emit_expected_tallies(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target_logger = logging.getLogger("tt_stats.test.cli")
    caplog.set_level(logging.INFO)
    started_at = datetime.now(tz=UTC)
    settled = [
        _Summary(key=1, received_count=4),
        _Summary(key=2, received_count=0),
    ]

    log_chunk_progress(
        target_logger,
        1,
        1,
        settled,
        started_at,
        event="test_chunk",
        unit="event",
        found_label="matches",
    )
    code = report_collect_run(
        settled,
        started_at=started_at,
        chunks_total=1,
        event="test_collect",
        unit="event",
        found_label="matches",
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "2 succeeded (1 with data, 1 empty), 4 matches found" in out
    assert "0 failed" in out

    failed_code = report_collect_run(
        [*settled, _Summary(key=3, received_count=0, error="boom")],
        started_at=started_at,
        chunks_total=1,
        event="test_collect",
        unit="event",
        found_label="matches",
    )
    assert failed_code == 1

    messages = [record.getMessage() for record in caplog.records]
    assert "test_chunk" in messages
    assert "test_collect_complete" in messages
    assert "test_collect_incomplete" in messages


def test_report_unpack_run_prints_and_logs_summary(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.INFO, logger="tt_stats.core.cli")

    code = report_unpack_run(
        [(2023, 101), (1934, 0)],
        unit="year",
        found_label="events",
        dry_run=True,
    )

    assert code == 0
    assert (
        "Unpack complete: 2 years, 101 rows (1 without events, dry_run=True)"
        in capsys.readouterr().out
    )
    records = [r for r in caplog.records if r.getMessage() == "unpack_complete"]
    assert len(records) == 1
    assert records[0].__dict__["years"] == 2
    assert records[0].__dict__["rows"] == 101
    assert records[0].__dict__["years_without_events"] == 1
    assert records[0].__dict__["dry_run"] is True

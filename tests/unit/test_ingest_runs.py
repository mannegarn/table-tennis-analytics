"""Unit tests for ingestion run status and fault-tolerant run recording."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from tt_stats.core.ingest_runs import (
    SOURCE_WTT_EVENTCALENDAR,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    RunRecord,
    safe_record_run,
    status_for,
)

if TYPE_CHECKING:
    import pytest


def _sample_run() -> RunRecord:
    return RunRecord(
        source=SOURCE_WTT_EVENTCALENDAR,
        entity_key="2023",
        status=STATUS_SUCCEEDED,
        started_at=datetime.now(tz=UTC),
        http_status=200,
        received_count=10,
    )


def test_status_for_maps_error_to_status() -> None:
    assert status_for(None) == STATUS_SUCCEEDED
    assert status_for("boom") == STATUS_FAILED


def test_safe_record_run_noop_when_callback_is_none() -> None:
    safe_record_run(None, _sample_run())


def test_safe_record_run_swallows_and_logs_callback_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="tt_stats.core.ingest_runs")

    def broken(_run: RunRecord) -> None:
        raise RuntimeError("disk full")

    safe_record_run(broken, _sample_run())

    records = [r for r in caplog.records if r.getMessage() == "ingest_run_not_recorded"]
    assert len(records) == 1
    assert records[0].__dict__["entity_key"] == "2023"
    assert "disk full" in records[0].__dict__["error"]

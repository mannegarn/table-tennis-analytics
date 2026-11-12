"""Append-only ingestion run history in ``ingest.ingest_runs``.

One row per fetch attempt, written once the attempt has settled. This is the
durable counterpart to the console log: it survives a closed terminal and can
be queried next to the data it describes.

It is deliberately not a collect flag. "Already fetched" stays row existence in
the raw table, and eligibility stays derived in the pending view (ADR 0005).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from tt_stats.core.logging_config import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from psycopg import Connection

logger = get_logger(__name__)

SOURCE_WTT_EVENTCALENDAR = "wtt_eventcalendar"
SOURCE_WTT_MATCH_ENTRIES = "wtt_getofficialresult"
SOURCE_WTT_EVENT_SPONSORS = "wtt_geteventequipmentwithlogo"

STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"


def status_for(error: str | None) -> str:
    """Return the run status implied by a failure message."""
    return STATUS_SUCCEEDED if error is None else STATUS_FAILED


@dataclass(frozen=True, slots=True)
class RunRecord:
    """One settled ingestion attempt.

    ``finished_at`` is not part of the record: it is stamped by the insert, so
    the recorded time is the write time rather than a caller's guess.
    """

    source: str
    entity_key: str
    status: str
    started_at: datetime
    http_status: int | None = None
    received_count: int | None = None
    payload_sha256: str | None = None
    error: str | None = None


def record_run(connection: Connection[Any], record: RunRecord) -> None:
    """Insert one attempt into ``ingest.ingest_runs`` in its own transaction.

    The caller passes a persistent ``autocommit=True`` connection. A run that
    failed mid-landing has already rolled its transaction back on that
    connection before this is called, so this transaction still commits even
    when the landing transaction failed.
    """
    with connection.transaction():
        connection.execute(
            """
            INSERT INTO ingest.ingest_runs (
                source,
                entity_key,
                status,
                started_at,
                finished_at,
                http_status,
                received_count,
                payload_sha256,
                error
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                record.source,
                record.entity_key,
                record.status,
                record.started_at,
                datetime.now(tz=UTC),
                record.http_status,
                record.received_count,
                record.payload_sha256,
                record.error,
            ),
        )


def safe_record_run(
    record: Callable[[RunRecord], None] | None,
    run: RunRecord,
) -> None:
    """Write an attempt via ``record``, swallowing diagnostic write failures."""
    if record is None:
        return

    try:
        record(run)
    except Exception as error:
        # Recording is diagnostics: losing a row must not lose the payload.
        logger.warning(
            "ingest_run_not_recorded",
            extra={
                "source": run.source,
                "entity_key": run.entity_key,
                "error": str(error),
            },
        )

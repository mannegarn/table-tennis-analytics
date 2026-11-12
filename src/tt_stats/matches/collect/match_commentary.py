"""Fetch and land one WTT match commentary payload per match, committed by chunk.

The endpoint returns a double-encoded JSON string containing an array of
commentary actions (or an empty array/string when a match has no commentary).
Each chunk is fetched concurrently, unwrapped into a JSON array, and committed
to ``ingest.match_commentary_raw``, ``ingest.match_commentary_unpacked``, and
``ingest.ingest_runs`` at the end of each chunk.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from tt_stats.core.batching import FetchedItem, collect_in_chunks, reason_counts
from tt_stats.core.cli import (
    add_queue_arguments,
    chunk_count,
    log_chunk_progress,
    progress_message,
    quiet_http_logs,
    report_collect_run,
    run_with_progress,
    validate_queue_arguments,
)
from tt_stats.core.clients.wtt_api import WttClient
from tt_stats.core.config import get_settings
from tt_stats.core.db import get_connection
from tt_stats.core.ingest_runs import (
    SOURCE_WTT_MATCH_COMMENTARY,
    RunRecord,
    record_runs,
    safe_record_runs,
    status_for,
)
from tt_stats.core.logging_config import configure_logging, get_logger
from tt_stats.matches.land import land_match_commentary_chunk

if TYPE_CHECKING:
    from psycopg import Connection

logger = get_logger(__name__)

MatchKey = tuple[int, str]

_PENDING_MATCHES_SQL = """
SELECT p.event_id, p.document_code, p.reason
FROM ingest.match_commentary_pending AS p
ORDER BY
    (p.reason = 'live') DESC,
    GREATEST(p.start_date, p.end_date) DESC,
    p.event_id,
    p.document_code;
"""

_PENDING_MATCHES_BY_EVENT_SQL = """
SELECT p.event_id, p.document_code, p.reason
FROM ingest.match_commentary_pending AS p
WHERE p.event_id = ANY(%(event_ids)s)
ORDER BY
    (p.reason = 'live') DESC,
    GREATEST(p.start_date, p.end_date) DESC,
    p.event_id,
    p.document_code;
"""


class PayloadShapeError(ValueError):
    """The response is not a list of commentary objects (or JSON-encoded list)."""


ChunkLandingFn = Callable[[Sequence[tuple["MatchCommentarySummary", Any]]], None]
RecordRunsFn = Callable[[Sequence[RunRecord]], None]
ProgressFn = Callable[["MatchCommentarySummary"], None]


@dataclass(frozen=True, slots=True)
class MatchCommentarySummary:
    """What happened for one match's commentary fetch."""

    event_id: int
    document_code: str
    status_code: int | None
    received_count: int
    payload_sha256: str | None
    fetched_at: datetime
    error: str | None
    reason: str | None = None


def pending_matches(
    connection: Connection[Any],
    limit: int | None = None,
    *,
    event_ids: Sequence[int] | None = None,
) -> list[tuple[MatchKey, str]]:
    """Return ``((event_id, document_code), reason)`` pairs in queue order."""
    if event_ids is not None:
        rows = connection.execute(
            _PENDING_MATCHES_BY_EVENT_SQL,
            {"event_ids": list(event_ids)},
        ).fetchall()
    else:
        rows = connection.execute(_PENDING_MATCHES_SQL).fetchall()

    queue = [
        ((int(row["event_id"]), str(row["document_code"])), str(row["reason"]))
        for row in rows
    ]
    return queue[:limit] if limit is not None else queue


def decode_commentary_payload(payload: object) -> list[dict[str, Any]]:
    """Unwrap the double-encoded string (if any) and return the list of rows."""
    unwrapped = payload
    if isinstance(unwrapped, str):
        stripped = unwrapped.strip()
        if not stripped:
            return []
        try:
            unwrapped = json.loads(stripped)
        except ValueError as error:
            raise PayloadShapeError(
                f"string payload is not valid inner JSON: {error}"
            ) from error

    if unwrapped is None:
        return []

    if not isinstance(unwrapped, list):
        raise PayloadShapeError(
            f"expected a list of commentary rows, got {type(unwrapped).__name__}"
        )

    rows: list[dict[str, Any]] = []
    for index, item in enumerate(cast("list[object]", unwrapped)):
        if not isinstance(item, dict):
            raise PayloadShapeError(f"row {index} is not an object")
        rows.append(cast("dict[str, Any]", item))

    return rows


async def collect_match_commentary(
    matches: Sequence[MatchKey],
    *,
    client: WttClient,
    progress: ProgressFn | None = None,
    land_chunk: ChunkLandingFn | None = None,
    record_many: RecordRunsFn | None = None,
    chunk_size: int | None = None,
    reasons: Mapping[MatchKey, str] | None = None,
) -> list[MatchCommentarySummary]:
    """Fetch match commentary concurrently by chunk, then land & record per chunk."""
    chunk_land_buffer: list[tuple[MatchCommentarySummary, Any]] = []
    chunk_run_buffer: list[RunRecord] = []

    def _settle(fetched: FetchedItem[MatchKey]) -> MatchCommentarySummary:
        event_id, document_code = fetched.key
        if fetched.response is None:
            summary = MatchCommentarySummary(
                event_id=event_id,
                document_code=document_code,
                status_code=fetched.status_code,
                received_count=0,
                payload_sha256=None,
                fetched_at=fetched.fetched_at,
                error=fetched.error or "request failed",
                reason=fetched.reason,
            )
            _log_summary(summary)
            chunk_run_buffer.append(
                _to_run_record(summary, started_at=fetched.started_at)
            )
            return summary

        response = fetched.response
        digest = hashlib.sha256(response.content).hexdigest()
        rows: list[dict[str, Any]] = []
        error_message: str | None = None

        try:
            rows = decode_commentary_payload(response.data)
        except PayloadShapeError as error:
            error_message = f"payload shape error: {error}"

        summary = MatchCommentarySummary(
            event_id=event_id,
            document_code=document_code,
            status_code=response.status_code,
            received_count=len(rows),
            payload_sha256=digest,
            fetched_at=fetched.fetched_at,
            error=error_message,
            reason=fetched.reason,
        )
        if summary.error is None:
            chunk_land_buffer.append((summary, rows))

        _log_summary(summary)
        chunk_run_buffer.append(_to_run_record(summary, started_at=fetched.started_at))
        return summary

    def _on_chunk(
        index: int,
        total: int,
        settled: list[MatchCommentarySummary],
        started_at: datetime,
    ) -> None:
        try:
            if chunk_land_buffer and land_chunk is not None:
                land_chunk(list(chunk_land_buffer))
        except Exception as error:
            logger.error(
                "match_commentary_chunk_landing_failed",
                extra={
                    "chunk": index,
                    "matches": len(chunk_land_buffer),
                    "error": str(error),
                },
            )
            raise
        finally:
            safe_record_runs(record_many, list(chunk_run_buffer))
            chunk_land_buffer.clear()
            chunk_run_buffer.clear()

        log_chunk_progress(
            logger,
            index,
            total,
            settled,
            started_at,
            event="match_commentary_chunk",
            unit="match",
            found_label="lines",
        )

    return await collect_in_chunks(
        matches,
        fetch=lambda key: client.fetch_match_commentary(key[0], key[1]),
        settle=_settle,
        on_chunk=_on_chunk,
        progress=progress,
        chunk_size=chunk_size,
        reasons=reasons,
    )


def _to_run_record(
    summary: MatchCommentarySummary,
    *,
    started_at: datetime,
) -> RunRecord:
    return RunRecord(
        source=SOURCE_WTT_MATCH_COMMENTARY,
        entity_key=f"{summary.event_id}:{summary.document_code}",
        status=status_for(summary.error),
        started_at=started_at,
        http_status=summary.status_code,
        received_count=summary.received_count,
        payload_sha256=summary.payload_sha256,
        error=summary.error,
    )


def _log_summary(summary: MatchCommentarySummary) -> None:
    extra: dict[str, Any] = {
        "event_id": summary.event_id,
        "document_code": summary.document_code,
        "reason": summary.reason,
        "status": summary.status_code,
        "received_count": summary.received_count,
    }
    if summary.error:
        logger.error(
            "match_commentary_incomplete",
            extra={**extra, "error": summary.error},
        )
    elif summary.received_count == 0:
        logger.debug("match_commentary_empty", extra=extra)
    else:
        logger.debug("match_commentary_saved", extra=extra)


async def _run(
    matches: Sequence[MatchKey],
    progress: ProgressFn | None,
    *,
    connection: Connection[Any],
    chunk_size: int,
    reasons: Mapping[MatchKey, str] | None,
) -> list[MatchCommentarySummary]:
    async with WttClient() as client:
        return await collect_match_commentary(
            matches,
            client=client,
            progress=progress,
            land_chunk=lambda items: land_match_commentary_chunk(connection, items),
            record_many=lambda runs: record_runs(connection, runs),
            chunk_size=chunk_size,
            reasons=reasons,
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-match-commentary-collect``."""
    parser = argparse.ArgumentParser(
        description=(
            "Fetch WTT match commentary into ingest.match_commentary_raw and "
            "ingest.match_commentary_unpacked, committed by chunk."
        )
    )
    parser.add_argument(
        "--event-ids",
        nargs="+",
        type=int,
        default=None,
        help="Optional event ids to filter the pending match_commentary queue.",
    )
    add_queue_arguments(parser, unit="match")
    args = parser.parse_args(argv)
    validate_queue_arguments(parser, args)

    configure_logging()
    settings = get_settings()
    quiet_http_logs(settings)

    chunk_size = args.chunk_size or settings.chunk_size
    started_at = datetime.now(tz=UTC)

    with get_connection(autocommit=True) as connection:
        queue = pending_matches(
            connection,
            args.limit,
            event_ids=args.event_ids,
        )
        matches = [key for key, _ in queue]
        reasons = dict(queue)
        logger.info(
            "match_commentary_queue",
            extra={"matches": len(matches), "reasons": reason_counts(queue)},
        )

        if not matches:
            logger.info("match_commentary_nothing_pending")
            print("Nothing pending: every match is already fetched.")  # noqa: T201
            return 0

        chunks_total = chunk_count(len(matches), chunk_size)
        logger.info(
            "match_commentary_started",
            extra={
                "matches": len(matches),
                "chunk_size": chunk_size,
                "chunks": chunks_total,
                "concurrency": settings.max_concurrent_requests,
            },
        )

        results = run_with_progress(
            len(matches),
            description="Collecting match commentary",
            unit="match",
            chunk_size=chunk_size,
            settings=settings,
            message=lambda summary: progress_message(
                summary,
                key=f"{summary.event_id}:{summary.document_code}",
                count_label="lines",
            ),
            run=lambda advance: _run(
                matches,
                advance,
                connection=connection,
                chunk_size=chunk_size,
                reasons=reasons or None,
            ),
        )

    return report_collect_run(
        results,
        started_at=started_at,
        chunks_total=chunks_total,
        event="match_commentary",
        unit="match",
        found_label="lines",
    )


if __name__ == "__main__":
    raise SystemExit(main())

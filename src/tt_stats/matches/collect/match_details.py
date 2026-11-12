"""Fetch and land one WTT matchcentre payload per match, committed by chunk.

The queue is derived in ``ingest.match_details_pending`` (ADR 0005). Because a
full backfill is ~200k matches, each chunk is fetched concurrently and then
written to ``ingest.match_details_raw`` (plus minimal player-id unpack) and
``ingest.ingest_runs`` in chunk-level transactions at the end of each chunk.
"""

from __future__ import annotations

import argparse
import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

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
    SOURCE_WTT_MATCH_DETAILS,
    RunRecord,
    record_runs,
    safe_record_runs,
    status_for,
)
from tt_stats.core.logging_config import configure_logging, get_logger
from tt_stats.matches.land import land_match_details_chunk

if TYPE_CHECKING:
    from psycopg import Connection

logger = get_logger(__name__)

MatchKey = tuple[int, str]

_PENDING_MATCHES_SQL = """
SELECT p.event_id, p.document_code, p.reason
FROM ingest.match_details_pending AS p
ORDER BY
    (p.reason = 'live') DESC,
    GREATEST(p.start_date, p.end_date) DESC,
    p.event_id,
    p.document_code;
"""

_PENDING_MATCHES_BY_EVENT_SQL = """
SELECT p.event_id, p.document_code, p.reason
FROM ingest.match_details_pending AS p
WHERE p.event_id = ANY(%(event_ids)s)
ORDER BY
    (p.reason = 'live') DESC,
    GREATEST(p.start_date, p.end_date) DESC,
    p.event_id,
    p.document_code;
"""


class PayloadShapeError(ValueError):
    """The response is not the JSON object expected from matchcentre."""


ChunkLandingFn = Callable[[Sequence[tuple["MatchDetailsSummary", Any]]], None]
RecordRunsFn = Callable[[Sequence[RunRecord]], None]
ProgressFn = Callable[["MatchDetailsSummary"], None]


@dataclass(frozen=True, slots=True)
class MatchDetailsSummary:
    """What happened for one match, including the evidence for a failure."""

    event_id: int
    document_code: str
    status_code: int | None
    #: Distinct player count parsed from ``competitiors`` -> ``players``.
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


def validate_payload(
    payload: object,
    *,
    event_id: int,
    document_code: str,
) -> tuple[int, str | None]:
    """Validate a matchcentre payload and return ``(player_count, error)``."""
    if not isinstance(payload, dict):
        raise PayloadShapeError(
            f"expected a match object, got {type(payload).__name__}"
        )

    if str(payload.get("eventId")) != str(event_id):
        return (
            0,
            f"eventId {payload.get('eventId')!r} does not match requested "
            f"event {event_id}",
        )

    if str(payload.get("documentCode")) != document_code:
        return (
            0,
            f"documentCode {payload.get('documentCode')!r} does not match "
            f"requested {document_code!r}",
        )

    player_ids: set[str] = set()
    competitors = payload.get("competitiors")
    if isinstance(competitors, list):
        for competitor in competitors:
            if not isinstance(competitor, dict):
                continue
            players = competitor.get("players")
            if not isinstance(players, list):
                continue
            for player in players:
                if isinstance(player, dict) and player.get("playerId") is not None:
                    player_ids.add(str(player["playerId"]))

    return len(player_ids), None


async def collect_match_details(
    matches: Sequence[MatchKey],
    *,
    client: WttClient,
    progress: ProgressFn | None = None,
    land_chunk: ChunkLandingFn | None = None,
    record_many: RecordRunsFn | None = None,
    chunk_size: int | None = None,
    reasons: Mapping[MatchKey, str] | None = None,
) -> list[MatchDetailsSummary]:
    """Fetch matches concurrently by chunk, then land & record at end of each chunk."""
    chunk_land_buffer: list[tuple[MatchDetailsSummary, Any]] = []
    chunk_run_buffer: list[RunRecord] = []

    def _settle(fetched: FetchedItem[MatchKey]) -> MatchDetailsSummary:
        event_id, document_code = fetched.key
        if fetched.response is None:
            if fetched.status_code == 404:
                summary = MatchDetailsSummary(
                    event_id=event_id,
                    document_code=document_code,
                    status_code=404,
                    received_count=0,
                    payload_sha256=hashlib.sha256(b"").hexdigest(),
                    fetched_at=fetched.fetched_at,
                    error=None,
                    reason=fetched.reason,
                )
                chunk_land_buffer.append((summary, {}))
                _log_summary(summary)
                chunk_run_buffer.append(
                    _to_run_record(summary, started_at=fetched.started_at)
                )
                return summary

            summary = MatchDetailsSummary(
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
        received_count = 0
        error_message: str | None = None

        try:
            received_count, error_message = validate_payload(
                response.data,
                event_id=event_id,
                document_code=document_code,
            )
        except PayloadShapeError as error:
            error_message = f"payload shape error: {error}"

        summary = MatchDetailsSummary(
            event_id=event_id,
            document_code=document_code,
            status_code=response.status_code,
            received_count=received_count,
            payload_sha256=digest,
            fetched_at=fetched.fetched_at,
            error=error_message,
            reason=fetched.reason,
        )
        if summary.error is None:
            chunk_land_buffer.append((summary, response.data))

        _log_summary(summary)
        chunk_run_buffer.append(_to_run_record(summary, started_at=fetched.started_at))
        return summary

    def _on_chunk(
        index: int,
        total: int,
        settled: list[MatchDetailsSummary],
        started_at: datetime,
    ) -> None:
        try:
            if chunk_land_buffer and land_chunk is not None:
                land_chunk(list(chunk_land_buffer))
        except Exception as error:
            logger.error(
                "match_details_chunk_landing_failed",
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
            event="match_details_chunk",
            unit="match",
            found_label="players",
        )

    return await collect_in_chunks(
        matches,
        fetch=lambda key: client.fetch_match_details(key[0], key[1]),
        settle=_settle,
        on_chunk=_on_chunk,
        progress=progress,
        chunk_size=chunk_size,
        reasons=reasons,
    )


def _to_run_record(
    summary: MatchDetailsSummary,
    *,
    started_at: datetime,
) -> RunRecord:
    return RunRecord(
        source=SOURCE_WTT_MATCH_DETAILS,
        entity_key=f"{summary.event_id}:{summary.document_code}",
        status=status_for(summary.error),
        started_at=started_at,
        http_status=summary.status_code,
        received_count=summary.received_count,
        payload_sha256=summary.payload_sha256,
        error=summary.error,
    )


def _log_summary(summary: MatchDetailsSummary) -> None:
    extra: dict[str, Any] = {
        "event_id": summary.event_id,
        "document_code": summary.document_code,
        "reason": summary.reason,
        "status": summary.status_code,
        "received_count": summary.received_count,
    }
    if summary.error:
        logger.error(
            "match_details_incomplete",
            extra={**extra, "error": summary.error},
        )
    else:
        logger.debug("match_details_saved", extra=extra)


async def _run(
    matches: Sequence[MatchKey],
    progress: ProgressFn | None,
    *,
    connection: Connection[Any],
    chunk_size: int,
    reasons: Mapping[MatchKey, str] | None,
) -> list[MatchDetailsSummary]:
    async with WttClient() as client:
        return await collect_match_details(
            matches,
            client=client,
            progress=progress,
            land_chunk=lambda items: land_match_details_chunk(connection, items),
            record_many=lambda runs: record_runs(connection, runs),
            chunk_size=chunk_size,
            reasons=reasons,
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-match-details-collect``."""
    parser = argparse.ArgumentParser(
        description=(
            "Fetch WTT matchcentre payloads into ingest.match_details_raw, "
            "landing and recording runs at the end of each chunk."
        )
    )
    parser.add_argument(
        "--event-ids",
        nargs="+",
        type=int,
        default=None,
        help="Optional event ids to filter the pending match_details queue.",
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
            "match_details_queue",
            extra={"matches": len(matches), "reasons": reason_counts(queue)},
        )

        if not matches:
            logger.info("match_details_nothing_pending")
            print("Nothing pending: every match is already fetched.")  # noqa: T201
            return 0

        chunks_total = chunk_count(len(matches), chunk_size)
        logger.info(
            "match_details_started",
            extra={
                "matches": len(matches),
                "chunk_size": chunk_size,
                "chunks": chunks_total,
                "concurrency": settings.max_concurrent_requests,
            },
        )

        results = run_with_progress(
            len(matches),
            description="Collecting match details",
            unit="match",
            chunk_size=chunk_size,
            settings=settings,
            message=lambda summary: progress_message(
                summary,
                key=f"{summary.event_id}:{summary.document_code}",
                count_label="players",
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
        event="match_details",
        unit="match",
        found_label="players",
    )


if __name__ == "__main__":
    raise SystemExit(main())

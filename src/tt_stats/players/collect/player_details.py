"""Fetch and land one WTT player_details payload per player_id."""

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
    SOURCE_WTT_PLAYER_DETAILS,
    RunRecord,
    record_runs,
    safe_record_runs,
    status_for,
)
from tt_stats.core.logging_config import configure_logging, get_logger
from tt_stats.players.land import land_player_details_chunk

if TYPE_CHECKING:
    from psycopg import Connection

logger = get_logger(__name__)

_PENDING_PLAYERS_SQL = """
SELECT p.player_id, p.reason
FROM ingest.player_details_pending AS p
ORDER BY p.player_id;
"""


class PayloadShapeError(ValueError):
    """The response is not the envelope expected from /Players/GetPlayers."""


LandingChunkFn = Callable[[Sequence[tuple["PlayerDetailsSummary", Any]]], None]
RecordManyFn = Callable[[Sequence[RunRecord]], None]
ProgressFn = Callable[["PlayerDetailsSummary"], None]


@dataclass(frozen=True, slots=True)
class PlayerDetailsSummary:
    """Outcome of fetching and validating one player's profile."""

    player_id: int
    status_code: int | None
    received_count: int
    payload_sha256: str | None
    fetched_at: datetime
    error: str | None
    reason: str | None = None


def pending_players(
    connection: Connection[Any],
    limit: int | None = None,
) -> list[tuple[int, str]]:
    """Return ``(player_id, reason)`` pairs in queue order."""
    rows = connection.execute(_PENDING_PLAYERS_SQL).fetchall()
    queue = [(int(row["player_id"]), str(row["reason"])) for row in rows]
    return queue[:limit] if limit is not None else queue


def validate_payload(
    payload: object,
    *,
    player_id: int,
) -> tuple[int, str | None]:
    """Validate a GetPlayers payload and return ``(profile_count, error)``."""
    if not isinstance(payload, dict):
        raise PayloadShapeError(
            f"expected a player envelope object, got {type(payload).__name__}"
        )

    result = payload.get("Result")
    if result is None:
        return 0, None
    if not isinstance(result, list):
        raise PayloadShapeError(
            f"expected Result list in player envelope, got {type(result).__name__}"
        )

    for index, item in enumerate(result):
        if not isinstance(item, dict):
            raise PayloadShapeError(
                f"expected object at Result[{index}], got {type(item).__name__}"
            )

    if result:
        returned_id = result[0].get("IttfId")
        if returned_id is not None and str(returned_id) != str(player_id):
            return (
                0,
                f"IttfId {returned_id!r} does not match requested player {player_id}",
            )

    return len(result), None


async def collect_player_details(
    players: Sequence[int],
    *,
    client: WttClient,
    land_chunk: LandingChunkFn | None = None,
    record_many: RecordManyFn | None = None,
    progress: ProgressFn | None = None,
    chunk_size: int = 50,
    reasons: Mapping[int, str] | None = None,
) -> list[PlayerDetailsSummary]:
    """Fetch player_details in chunks, land and unpack each completed chunk."""
    chunk_land_buffer: list[tuple[PlayerDetailsSummary, Any]] = []
    chunk_run_buffer: list[RunRecord] = []

    def _settle(fetched: FetchedItem[int]) -> PlayerDetailsSummary:
        player_id = fetched.key
        if fetched.response is None:
            if fetched.status_code == 404:
                summary = PlayerDetailsSummary(
                    player_id=player_id,
                    status_code=404,
                    received_count=0,
                    payload_sha256=hashlib.sha256(b"").hexdigest(),
                    fetched_at=fetched.fetched_at,
                    error=None,
                    reason=fetched.reason,
                )
                chunk_land_buffer.append((summary, {"Result": []}))
                _log_summary(summary)
                chunk_run_buffer.append(
                    _to_run_record(summary, started_at=fetched.started_at)
                )
                return summary

            summary = PlayerDetailsSummary(
                player_id=player_id,
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
                player_id=player_id,
            )
        except PayloadShapeError as error:
            error_message = f"payload shape error: {error}"

        summary = PlayerDetailsSummary(
            player_id=player_id,
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
        settled: list[PlayerDetailsSummary],
        started_at: datetime,
    ) -> None:
        try:
            if chunk_land_buffer and land_chunk is not None:
                land_chunk(list(chunk_land_buffer))
        except Exception as error:
            logger.error(
                "player_details_chunk_landing_failed",
                extra={
                    "chunk": index,
                    "players": len(chunk_land_buffer),
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
            event="player_details_chunk",
            unit="player",
            found_label="profiles",
        )

    return await collect_in_chunks(
        players,
        fetch=client.fetch_player_details,
        settle=_settle,
        on_chunk=_on_chunk,
        progress=progress,
        chunk_size=chunk_size,
        reasons=reasons,
    )


def _to_run_record(
    summary: PlayerDetailsSummary,
    *,
    started_at: datetime,
) -> RunRecord:
    return RunRecord(
        source=SOURCE_WTT_PLAYER_DETAILS,
        entity_key=str(summary.player_id),
        status=status_for(summary.error),
        started_at=started_at,
        http_status=summary.status_code,
        received_count=summary.received_count,
        payload_sha256=summary.payload_sha256,
        error=summary.error,
    )


def _log_summary(summary: PlayerDetailsSummary) -> None:
    extra: dict[str, Any] = {
        "player_id": summary.player_id,
        "reason": summary.reason,
        "status": summary.status_code,
        "received_count": summary.received_count,
    }
    if summary.error:
        logger.error(
            "player_details_incomplete",
            extra={**extra, "error": summary.error},
        )
    elif summary.received_count == 0:
        logger.debug("player_details_empty", extra=extra)
    else:
        logger.debug("player_details_saved", extra=extra)


async def _run(
    players: Sequence[int],
    progress: ProgressFn | None,
    *,
    connection: Connection[Any],
    chunk_size: int,
    reasons: Mapping[int, str] | None,
) -> list[PlayerDetailsSummary]:
    async with WttClient() as client:
        return await collect_player_details(
            players,
            client=client,
            progress=progress,
            land_chunk=lambda items: land_player_details_chunk(connection, items),
            record_many=lambda runs: record_runs(connection, runs),
            chunk_size=chunk_size,
            reasons=reasons,
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-player-details-collect``."""
    parser = argparse.ArgumentParser(
        description=(
            "Fetch WTT player details into ingest.player_details_raw and "
            "ingest.player_details_unpacked, committed by chunk."
        )
    )
    parser.add_argument(
        "--player-ids",
        nargs="+",
        type=int,
        default=None,
        help="Explicit player IDs to fetch, bypassing the pending view.",
    )
    add_queue_arguments(parser, unit="player")
    args = parser.parse_args(argv)
    validate_queue_arguments(parser, args)

    configure_logging()
    settings = get_settings()
    quiet_http_logs(settings)

    chunk_size = args.chunk_size or settings.chunk_size
    started_at = datetime.now(tz=UTC)

    with get_connection(autocommit=True) as connection:
        reasons: dict[int, str] = {}
        if args.player_ids is not None:
            players = list(args.player_ids)
            logger.info("player_details_manual", extra={"players": len(players)})
        else:
            queue = pending_players(connection, args.limit)
            players = [player_id for player_id, _ in queue]
            reasons = dict(queue)
            logger.info(
                "player_details_queue",
                extra={"players": len(players), "reasons": reason_counts(queue)},
            )

        if not players:
            logger.info("player_details_nothing_pending")
            print("Nothing pending: every player is already fetched.")  # noqa: T201
            return 0

        chunks_total = chunk_count(len(players), chunk_size)
        logger.info(
            "player_details_started",
            extra={
                "players": len(players),
                "chunk_size": chunk_size,
                "chunks": chunks_total,
                "concurrency": settings.max_concurrent_requests,
            },
        )

        results = run_with_progress(
            len(players),
            description="Collecting player details",
            unit="player",
            chunk_size=chunk_size,
            settings=settings,
            message=lambda summary: progress_message(
                summary,
                key=f"player={summary.player_id}",
                count_label="profiles",
            ),
            run=lambda advance: _run(
                players,
                advance,
                connection=connection,
                chunk_size=chunk_size,
                reasons=reasons or None,
            ),
            reasons=reasons or None,
        )

    return report_collect_run(
        results,
        started_at=started_at,
        chunks_total=chunks_total,
        event="player_details",
        unit="player",
        found_label="profiles",
        raw_table="ingest.player_details_raw",
        unpacked_table="ingest.player_details_unpacked",
    )


if __name__ == "__main__":
    raise SystemExit(main())

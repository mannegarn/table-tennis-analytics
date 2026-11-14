"""Land player_details payloads in chunked transactions, with SQL unpack."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from psycopg.types.json import Jsonb

from tt_stats.core.logging_config import get_logger
from tt_stats.core.sql_files import load_sql

if TYPE_CHECKING:
    from collections.abc import Sequence

    from psycopg import Connection

    from tt_stats.players.collect.player_details import PlayerDetailsSummary

logger = get_logger(__name__)

UNPACK_PLAYER_DETAILS_SQL_PATH = (
    Path(__file__).with_name("sql") / "unpack_player_details.sql"
)


def land_player_details(
    connection: Connection[Any],
    summary: PlayerDetailsSummary,
    payload: Any,
    *,
    unpack: bool = True,
) -> None:
    """Upsert one player's raw details payload and unpack it."""
    land_player_details_chunk(connection, [(summary, payload)], unpack=unpack)


def land_player_details_chunk(
    connection: Connection[Any],
    items: Sequence[tuple[PlayerDetailsSummary, Any]],
    *,
    unpack: bool = True,
) -> None:
    """Upsert a chunk of player_details payloads in a single transaction."""
    if not items:
        return

    raw_params: list[tuple[Any, ...]] = []
    unpack_params: list[dict[str, Any]] = []
    for summary, payload in items:
        if summary.error is not None:
            raise ValueError(f"cannot land a failed player: {summary.error}")
        if summary.status_code is None or summary.payload_sha256 is None:
            raise ValueError("summary is missing status or payload hash")

        raw_params.append(
            (
                summary.player_id,
                Jsonb(payload),
                summary.status_code,
                summary.received_count,
                summary.payload_sha256,
                summary.fetched_at,
            )
        )
        unpack_params.append({"player_id": summary.player_id})

    unpack_sql = load_sql(UNPACK_PLAYER_DETAILS_SQL_PATH) if unpack else None

    with connection.transaction(), connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO ingest.player_details_raw (
                player_id,
                payload,
                http_status,
                received_count,
                payload_sha256,
                fetched_at
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (player_id) DO UPDATE SET
                payload = EXCLUDED.payload,
                http_status = EXCLUDED.http_status,
                received_count = EXCLUDED.received_count,
                payload_sha256 = EXCLUDED.payload_sha256,
                fetched_at = EXCLUDED.fetched_at,
                ingested_at = now();
            """,
            raw_params,
        )

        if unpack_sql is not None:
            cursor.executemany(unpack_sql.encode("utf-8"), unpack_params)

    logger.debug("player_details_chunk_landed", extra={"players": len(items)})

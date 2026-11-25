"""Land match_details payloads in chunked transactions, with minimal player unpack."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from psycopg.types.json import Jsonb

from tt_stats.core.logging_config import get_logger
from tt_stats.core.sql_files import load_sql

if TYPE_CHECKING:
    from collections.abc import Sequence

    from psycopg import Connection

    from tt_stats.core.collector import FetchSummary

logger = get_logger(__name__)

UNPACK_PLAYERS_SQL_PATH = (
    Path(__file__).with_name("sql") / "unpack_match_details_players.sql"
)


def land_match_details(
    connection: Connection[Any],
    summary: FetchSummary[tuple[int, str]],
    payload: Any,
    *,
    unpack: bool = True,
) -> None:
    """Upsert one match's detail payload (and optionally unpack player ids)."""
    land_match_details_chunk(connection, [(summary, payload)], unpack=unpack)


def land_match_details_chunk(
    connection: Connection[Any],
    items: Sequence[tuple[FetchSummary[tuple[int, str]], Any]],
    *,
    unpack: bool = True,
) -> None:
    """Upsert a chunk of match_details payloads in a single transaction."""
    if not items:
        return

    raw_params: list[tuple[Any, ...]] = []
    unpack_params: list[dict[str, Any]] = []
    for summary, payload in items:
        if summary.error is not None:
            raise ValueError(f"cannot land a failed match: {summary.error}")
        if summary.status_code is None or summary.payload_sha256 is None:
            raise ValueError("summary is missing status or payload hash")

        event_id, document_code = summary.key
        raw_params.append(
            (
                event_id,
                document_code,
                Jsonb(payload),
                summary.status_code,
                summary.payload_sha256,
                summary.fetched_at,
            )
        )
        unpack_params.append(
            {
                "event_id": event_id,
                "document_code": document_code,
            }
        )

    unpack_sql = load_sql(UNPACK_PLAYERS_SQL_PATH) if unpack else None

    blocked: list[FetchSummary[tuple[int, str]]] = []
    with connection.transaction(), connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO ingest_wtt.match_details_raw (
                event_id,
                document_code,
                payload,
                http_status,
                payload_sha256,
                fetched_at
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_id, document_code) DO UPDATE SET
                payload = EXCLUDED.payload,
                http_status = EXCLUDED.http_status,
                payload_sha256 = EXCLUDED.payload_sha256,
                fetched_at = EXCLUDED.fetched_at,
                ingested_at = now()
            WHERE ingest_wtt.match_details_raw.payload = '{}'::jsonb
               OR EXCLUDED.payload <> '{}'::jsonb
            RETURNING 1;
            """,
            raw_params,
            returning=True,
        )

        landed_unpack_params: list[dict[str, Any]] = []
        for index, ((summary, _payload), unpack_param) in enumerate(
            zip(items, unpack_params, strict=True)
        ):
            if index > 0 and not cursor.nextset():
                break
            if cursor.fetchone() is not None:
                landed_unpack_params.append(unpack_param)
            else:
                blocked.append(summary)

        if unpack_sql is not None and landed_unpack_params:
            cursor.executemany(unpack_sql.encode("utf-8"), landed_unpack_params)

    for summary in blocked:
        event_id, document_code = summary.key
        logger.warning(
            "match_details_empty_overwrite_blocked",
            extra={
                "event_id": event_id,
                "document_code": document_code,
                "http_status": summary.status_code,
                "reason": summary.reason,
            },
        )

    logger.debug(
        "match_details_chunk_landed",
        extra={"matches": len(items) - len(blocked), "blocked": len(blocked)},
    )

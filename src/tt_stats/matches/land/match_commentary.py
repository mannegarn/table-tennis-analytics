"""Land match_commentary payloads in chunked transactions, with SQL unpack."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from psycopg.types.json import Jsonb

from tt_stats.core.logging_config import get_logger
from tt_stats.core.sql_files import load_sql

if TYPE_CHECKING:
    from collections.abc import Sequence

    from psycopg import Connection

    from tt_stats.matches.collect.match_commentary import MatchCommentarySummary

logger = get_logger(__name__)

UNPACK_COMMENTARY_SQL_PATH = (
    Path(__file__).with_name("sql") / "unpack_match_commentary.sql"
)


def land_match_commentary(
    connection: Connection[Any],
    summary: MatchCommentarySummary,
    payload: Any,
    *,
    unpack: bool = True,
) -> None:
    """Upsert one match's decoded commentary payload and unpack it."""
    land_match_commentary_chunk(connection, [(summary, payload)], unpack=unpack)


def land_match_commentary_chunk(
    connection: Connection[Any],
    items: Sequence[tuple[MatchCommentarySummary, Any]],
    *,
    unpack: bool = True,
) -> None:
    """Upsert a chunk of match_commentary payloads in a single transaction."""
    if not items:
        return

    raw_params: list[tuple[Any, ...]] = []
    unpack_params: list[dict[str, Any]] = []
    for summary, payload in items:
        if summary.error is not None:
            raise ValueError(f"cannot land a failed match: {summary.error}")
        if summary.status_code is None or summary.payload_sha256 is None:
            raise ValueError("summary is missing status or payload hash")

        raw_params.append(
            (
                summary.event_id,
                summary.document_code,
                Jsonb(payload),
                summary.status_code,
                summary.received_count,
                summary.payload_sha256,
                summary.fetched_at,
            )
        )
        unpack_params.append(
            {
                "event_id": summary.event_id,
                "document_code": summary.document_code,
            }
        )

    unpack_sql = load_sql(UNPACK_COMMENTARY_SQL_PATH) if unpack else None

    with connection.transaction(), connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO ingest.match_commentary_raw (
                event_id,
                document_code,
                payload,
                http_status,
                received_count,
                payload_sha256,
                fetched_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_id, document_code) DO UPDATE SET
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

    logger.debug("match_commentary_chunk_landed", extra={"matches": len(items)})

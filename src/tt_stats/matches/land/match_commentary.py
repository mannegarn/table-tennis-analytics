"""Land match_commentary payloads in chunked transactions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from psycopg.types.json import Jsonb

from tt_stats.core.logging_config import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from psycopg import Connection

    from tt_stats.core.collector import FetchSummary

logger = get_logger(__name__)


def land_match_commentary(
    connection: Connection[Any],
    summary: FetchSummary[tuple[int, str]],
    payload: Any,
) -> None:
    """Upsert one match's decoded commentary payload."""
    land_match_commentary_chunk(connection, [(summary, payload)])


def land_match_commentary_chunk(
    connection: Connection[Any],
    items: Sequence[tuple[FetchSummary[tuple[int, str]], Any]],
) -> None:
    """Upsert a chunk of match_commentary payloads in a single transaction."""
    if not items:
        return

    raw_params: list[tuple[Any, ...]] = []
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
                summary.received_count,
                summary.payload_sha256,
                summary.fetched_at,
            )
        )

    blocked: list[FetchSummary[tuple[int, str]]] = []
    with connection.transaction(), connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO ingest_wtt.match_commentary_raw (
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
                ingested_at = now()
            WHERE ingest_wtt.match_commentary_raw.received_count = 0
               OR EXCLUDED.received_count > 0
            RETURNING 1;
            """,
            raw_params,
            returning=True,
        )

        for index, (summary, _payload) in enumerate(items):
            if index > 0 and not cursor.nextset():
                break
            if cursor.fetchone() is None:
                blocked.append(summary)

    for summary in blocked:
        event_id, document_code = summary.key
        logger.warning(
            "match_commentary_empty_overwrite_blocked",
            extra={
                "event_id": event_id,
                "document_code": document_code,
                "http_status": summary.status_code,
                "reason": summary.reason,
            },
        )

    logger.debug(
        "match_commentary_chunk_landed",
        extra={"matches": len(items) - len(blocked), "blocked": len(blocked)},
    )

"""Seed ingest_wtt.match_details_raw from local per-event backup JSON files."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from psycopg.types.json import Jsonb

from tt_stats.core.batching import chunks
from tt_stats.core.config import get_settings
from tt_stats.core.db import get_connection
from tt_stats.core.logging_config import configure_logging, get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from psycopg import Connection

logger = get_logger(__name__)

DEFAULT_BACKUP_SUBDIR = Path("raw") / "match_details_backup"
DEFAULT_SOURCE = "wtt_backup"
DEFAULT_BATCH_SIZE = 500

_UPSERT_SQL = """
INSERT INTO ingest_wtt.match_details_raw (
    event_id,
    document_code,
    payload,
    http_status,
    payload_sha256,
    source,
    fetched_at
)
VALUES (%s, %s, %s, 200, %s, %s, %s)
ON CONFLICT (event_id, document_code) DO UPDATE SET
    payload        = EXCLUDED.payload,
    http_status    = EXCLUDED.http_status,
    payload_sha256 = EXCLUDED.payload_sha256,
    source         = EXCLUDED.source,
    fetched_at     = EXCLUDED.fetched_at,
    ingested_at    = now()
WHERE ingest_wtt.match_details_raw.http_status = 404
   OR ingest_wtt.match_details_raw.payload = '{}'::jsonb;
"""


@dataclass(frozen=True, slots=True)
class BackupMatch:
    """One validated match payload parsed from a per-event backup JSON file."""

    event_id: int
    document_code: str
    payload: dict[str, Any]
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class SeedResult:
    """Counts returned by ``seed_match_details_backup``."""

    files: int
    matches_seen: int
    rows_written: int


def _parse_event_id(value: object, path: Path) -> int:
    if isinstance(value, bool):
        raise ValueError(f"invalid eventId in {path}: {value!r}")
    if isinstance(value, int):
        event_id = value
    elif isinstance(value, str) and value.strip().isdigit():
        event_id = int(value.strip())
    else:
        raise ValueError(f"invalid eventId in {path}: {value!r}")

    if event_id <= 0:
        raise ValueError(f"eventId must be positive in {path}, got {event_id}")
    return event_id


def _parse_document_code(value: object, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"invalid documentCode in {path}: {value!r}")
    return value.strip()


def load_backup_file(path: Path) -> list[BackupMatch]:
    """Read and validate all match objects from one per-event JSON file."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"expected a JSON list in {path}, got {type(raw).__name__}")

    matches: list[BackupMatch] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(
                f"expected match dict in {path}, got {type(item).__name__}"
            )
        event_id = _parse_event_id(item.get("eventId"), path)
        document_code = _parse_document_code(item.get("documentCode"), path)
        canonical = json.dumps(item, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        matches.append(
            BackupMatch(
                event_id=event_id,
                document_code=document_code,
                payload=item,
                payload_sha256=hashlib.sha256(canonical).hexdigest(),
            )
        )
    return matches


def seed_match_details_backup(
    connection: Connection[Any],
    backup_dir: Path,
    *,
    source: str = DEFAULT_SOURCE,
    fetched_at: datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> SeedResult:
    """Load raw backup match JSONs into ``ingest_wtt.match_details_raw`` only.

    Inserts missing matches and replaces existing 404 or empty ``{}`` rows.
    Live-scraped 200 rows are never overwritten, and no unpacking is performed.
    """
    if batch_size < 1:
        raise ValueError(f"batch_size must be at least 1, got {batch_size}")

    resolved_fetched_at = fetched_at or datetime.now(tz=UTC)
    files = sorted(backup_dir.glob("*.json"))
    matches_seen = 0
    rows_written = 0

    for path in files:
        matches = load_backup_file(path)
        matches_seen += len(matches)
        if not matches:
            continue

        for batch in chunks(matches, batch_size):
            params = [
                (
                    match.event_id,
                    match.document_code,
                    Jsonb(match.payload),
                    match.payload_sha256,
                    source,
                    resolved_fetched_at,
                )
                for match in batch
            ]
            with connection.transaction(), connection.cursor() as cursor:
                cursor.executemany(_UPSERT_SQL, params)
                if cursor.rowcount > 0:
                    rows_written += cursor.rowcount

    logger.info(
        "match_details_backup_seeded",
        extra={
            "files": len(files),
            "matches_seen": matches_seen,
            "rows_written": rows_written,
            "source": source,
        },
    )
    return SeedResult(
        files=len(files),
        matches_seen=matches_seen,
        rows_written=rows_written,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-match-details-seed``."""
    settings = get_settings()
    default_dir = settings.data_dir / DEFAULT_BACKUP_SUBDIR

    parser = argparse.ArgumentParser(
        description=(
            "Seed ingest_wtt.match_details_raw from local per-event backup JSONs."
        )
    )
    parser.add_argument(
        "--dir",
        dest="backup_dir",
        type=Path,
        default=default_dir,
        help=f"Directory of backup JSON files (default: {default_dir}).",
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help=f"Provenance tag for seeded rows (default: {DEFAULT_SOURCE}).",
    )
    args = parser.parse_args(argv)

    configure_logging()

    if not args.backup_dir.is_dir():
        parser.error(f"backup directory not found: {args.backup_dir}")

    with get_connection(autocommit=True) as connection:
        result = seed_match_details_backup(
            connection,
            args.backup_dir,
            source=args.source,
        )

    print(  # noqa: T201
        f"Processed {result.files:,} files ({result.matches_seen:,} matches); "
        f"wrote {result.rows_written:,} rows to ingest_wtt.match_details_raw."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

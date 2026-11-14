"""Seed ingest.ittf_player_profiles from a local master-players CSV file."""

from __future__ import annotations

import argparse
import csv
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tt_stats.core.config import get_settings
from tt_stats.core.db import get_connection
from tt_stats.core.logging_config import configure_logging, get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from psycopg import Connection

logger = get_logger(__name__)

DEFAULT_CSV_NAME = "20260615_master_players.csv"


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def seed_ittf_player_profiles(
    connection: Connection[Any],
    csv_path: Path,
    *,
    source: str | None = None,
    fetched_at: datetime | None = None,
) -> int:
    """Load style, grip, and hand rows from ``csv_path`` into Postgres.

    Existing rows whose ``source`` is ``'ittf_results_link'`` are preserved so
    re-running the local seed never overwrites live-scraped profiles.
    """
    raw_bytes = csv_path.read_bytes()
    file_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    resolved_source = source or csv_path.name
    resolved_fetched_at = fetched_at or datetime.now(tz=UTC)

    params: list[tuple[Any, ...]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            raw_id = (row.get("playerId") or "").strip()
            if not raw_id.isdigit():
                raise ValueError(f"invalid playerId in {csv_path}: {raw_id!r}")
            player_id = int(raw_id)
            if player_id <= 0:
                raise ValueError(f"playerId must be positive, got {player_id}")

            params.append(
                (
                    player_id,
                    _clean_text(row.get("Style")),
                    _clean_text(row.get("Grip")),
                    _clean_text(row.get("Hand")),
                    resolved_source,
                    None,
                    file_sha256,
                    resolved_fetched_at,
                )
            )

    if not params:
        return 0

    with connection.transaction(), connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO ingest.ittf_player_profiles (
                player_id,
                style,
                grip,
                hand,
                source,
                http_status,
                payload_sha256,
                fetched_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (player_id) DO UPDATE SET
                style          = EXCLUDED.style,
                grip           = EXCLUDED.grip,
                hand           = EXCLUDED.hand,
                source         = EXCLUDED.source,
                payload_sha256 = EXCLUDED.payload_sha256,
                fetched_at     = EXCLUDED.fetched_at,
                ingested_at    = now()
            WHERE ingest.ittf_player_profiles.source <> 'ittf_results_link';
            """,
            params,
        )

    logger.info(
        "ittf_player_profiles_seeded",
        extra={
            "rows": len(params),
            "source": resolved_source,
            "sha256": file_sha256,
        },
    )
    return len(params)


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-ittf-players-seed``."""
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description=(
            "Seed ingest.ittf_player_profiles from a local master players CSV."
        )
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=settings.data_dir / DEFAULT_CSV_NAME,
        help=f"Path to the CSV file (default: {settings.data_dir / DEFAULT_CSV_NAME}).",
    )
    args = parser.parse_args(argv)

    configure_logging()

    if not args.csv.is_file():
        parser.error(f"CSV file not found: {args.csv}")

    with get_connection(autocommit=True) as connection:
        count = seed_ittf_player_profiles(connection, args.csv)

    print(f"Seeded {count:,} player profiles from {args.csv.name}.")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

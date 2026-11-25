"""Sync players missing hand/grip/style into player_profile_overrides.csv."""

from __future__ import annotations

import argparse
import csv
import io
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from psycopg import Connection

from tt_stats.core.config import project_root
from tt_stats.core.db import get_connection
from tt_stats.core.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

DEFAULT_CSV_PATH = project_root() / "dbt" / "seeds" / "player_profile_overrides.csv"

CSV_FIELDNAMES: tuple[str, ...] = (
    "ittf_id",
    "player_name",
    "dob",
    "hand",
    "grip",
    "style",
    "notes",
)

_MISSING_PROFILES_QUERY = """
select
    p.ittf_id,
    p.player_name,
    p.dob,
    p.hand,
    p.grip,
    p.style,
    (
        p.hand is null
        or p.grip is null
        or p.style is null
    ) as is_missing_profile
from marts_wtt.dim_players as p
where
    p.hand is null
    or p.grip is null
    or p.style is null
    or p.ittf_id = any(%s)
order by p.ittf_id asc
"""


@dataclass(frozen=True)
class PlayerProfileMetadata:
    """Warehouse metadata for one player used to backfill/append CSV rows."""

    ittf_id: str
    player_name: str
    dob: date | None
    hand: str | None
    grip: str | None
    style: str | None
    is_missing_profile: bool


@dataclass(frozen=True)
class SyncResult:
    """Summary of one player-profile-overrides CSV sync."""

    csv_path: Path
    existing_rows: int
    added_rows: int
    backfilled_rows: int
    total_rows: int
    wrote_file: bool
    skipped_reason: str | None = None


def _sort_key(ittf_id: str) -> tuple[int, int, str]:
    if ittf_id.isdigit():
        return (0, int(ittf_id), ittf_id)
    return (1, 0, ittf_id)


def read_profile_overrides_csv(csv_path: Path) -> list[dict[str, str]]:
    """Read existing rows from ``player_profile_overrides.csv`` if present."""
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            {k: (v or "").strip() for k, v in row.items() if k is not None}
            for row in reader
            if (row.get("ittf_id") or "").strip()
        ]


def merge_profile_override_rows(
    existing_rows: Sequence[Mapping[str, str]],
    warehouse_players: Iterable[PlayerProfileMetadata],
) -> tuple[list[dict[str, str]], int, int]:
    """Merge existing manual rows with players missing hand, grip, or style.

    Rules:
    - Never drop an existing row from the CSV.
    - Never overwrite non-empty ``player_name``, ``dob``, ``hand``, ``grip``,
      ``style``, or ``notes`` on existing rows.
    - Backfill blank ``player_name`` / ``dob`` on existing rows from
      ``warehouse_players`` when available.
    - Append players with ``is_missing_profile=True`` not yet in the CSV,
      pre-filling any already-known ``hand``, ``grip``, or ``style`` values.
    - Return rows sorted by ``ittf_id`` ascending, plus ``(added, backfilled)``.
    """
    meta_by_id: dict[str, PlayerProfileMetadata] = {
        pl.ittf_id: pl for pl in warehouse_players
    }
    merged: dict[str, dict[str, str]] = {}
    backfilled = 0

    for raw in existing_rows:
        ittf_id = raw["ittf_id"].strip()
        meta = meta_by_id.get(ittf_id)

        existing_name = raw.get("player_name", "").strip()
        existing_dob = raw.get("dob", "").strip()

        player_name = existing_name
        if not player_name and meta is not None:
            player_name = meta.player_name

        dob_str = existing_dob
        if not dob_str and meta is not None and meta.dob is not None:
            dob_str = meta.dob.isoformat()

        if player_name != existing_name or dob_str != existing_dob:
            backfilled += 1

        merged[ittf_id] = {
            "ittf_id": ittf_id,
            "player_name": player_name,
            "dob": dob_str,
            "hand": raw.get("hand", "").strip(),
            "grip": raw.get("grip", "").strip(),
            "style": raw.get("style", "").strip(),
            "notes": raw.get("notes", "").strip(),
        }

    added = 0
    for ittf_id in sorted(meta_by_id, key=_sort_key):
        meta = meta_by_id[ittf_id]
        if not meta.is_missing_profile or ittf_id in merged:
            continue
        merged[ittf_id] = {
            "ittf_id": ittf_id,
            "player_name": meta.player_name,
            "dob": meta.dob.isoformat() if meta.dob else "",
            "hand": meta.hand or "",
            "grip": meta.grip or "",
            "style": meta.style or "",
            "notes": "",
        }
        added += 1

    ordered_rows = [merged[pid] for pid in sorted(merged, key=_sort_key)]
    return ordered_rows, added, backfilled


def render_profile_overrides_csv(rows: Sequence[Mapping[str, str]]) -> str:
    """Serialize merged rows to deterministic UTF-8 CSV text (LF newlines)."""
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=CSV_FIELDNAMES,
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _required_table_exists(conn: Connection[Any]) -> bool:
    """Return True when ``marts_wtt.dim_players`` exists."""
    row = conn.execute(
        """
        select to_regclass('marts_wtt.dim_players') is not null as has_dim_players
        """
    ).fetchone()
    if row is None:
        return False
    return bool(row["has_dim_players"])


def fetch_player_profile_metadata(
    conn: Connection[Any],
    existing_ittf_ids: Sequence[str],
) -> list[PlayerProfileMetadata] | None:
    """Fetch players missing profile attributes plus metadata for existing CSV rows.

    Returns ``None`` if ``marts_wtt.dim_players`` has not been built yet.
    """
    if not _required_table_exists(conn):
        return None
    rows = conn.execute(
        _MISSING_PROFILES_QUERY,
        (list(existing_ittf_ids),),
    ).fetchall()
    return [
        PlayerProfileMetadata(
            ittf_id=str(r["ittf_id"]),
            player_name=str(r["player_name"] or ""),
            dob=r["dob"],
            hand=r["hand"],
            grip=r["grip"],
            style=r["style"],
            is_missing_profile=bool(r["is_missing_profile"]),
        )
        for r in rows
    ]


def sync_profile_overrides_csv(
    csv_path: Path = DEFAULT_CSV_PATH,
    conn: Connection[Any] | None = None,
) -> SyncResult:
    """Sync players missing hand/grip/style into ``player_profile_overrides.csv``."""
    existing_rows = read_profile_overrides_csv(csv_path)
    existing_ids = [r["ittf_id"] for r in existing_rows]

    if conn is None:
        with get_connection() as managed_conn:
            metadata = fetch_player_profile_metadata(managed_conn, existing_ids)
    else:
        metadata = fetch_player_profile_metadata(conn, existing_ids)

    if metadata is None:
        reason = "marts_wtt.dim_players not built yet"
        logger.warning("Skipping player profile overrides CSV sync: %s", reason)
        return SyncResult(
            csv_path=csv_path,
            existing_rows=len(existing_rows),
            added_rows=0,
            backfilled_rows=0,
            total_rows=len(existing_rows),
            wrote_file=False,
            skipped_reason=reason,
        )

    merged_rows, added, backfilled = merge_profile_override_rows(
        existing_rows, metadata
    )
    rendered = render_profile_overrides_csv(merged_rows)
    previous = csv_path.read_text(encoding="utf-8") if csv_path.exists() else ""

    if rendered == previous:
        logger.info(
            "player_profile_overrides.csv already up to date (%d rows)",
            len(merged_rows),
        )
        return SyncResult(
            csv_path=csv_path,
            existing_rows=len(existing_rows),
            added_rows=0,
            backfilled_rows=0,
            total_rows=len(merged_rows),
            wrote_file=False,
        )

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(rendered, encoding="utf-8")
    logger.info(
        "Synced %s: %d total (+%d added, %d backfilled)",
        csv_path,
        len(merged_rows),
        added,
        backfilled,
    )
    return SyncResult(
        csv_path=csv_path,
        existing_rows=len(existing_rows),
        added_rows=added,
        backfilled_rows=backfilled,
        total_rows=len(merged_rows),
        wrote_file=True,
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Append players missing hand, grip, or style to "
            "dbt/seeds/player_profile_overrides.csv without overwriting manual edits."
        )
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help=f"Target player profile overrides CSV path (default: {DEFAULT_CSV_PATH})",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-player-profile-overrides-sync``."""
    configure_logging()
    args = _parse_args(argv)
    result = sync_profile_overrides_csv(args.csv)
    if result.skipped_reason:
        print(  # noqa: T201
            f"Skipped player profile overrides sync: {result.skipped_reason}"
        )
    elif result.wrote_file:
        print(  # noqa: T201
            f"Synced {result.csv_path}: {result.total_rows} rows "
            f"(+{result.added_rows} added, {result.backfilled_rows} backfilled)"
        )
    else:
        print(  # noqa: T201
            f"{result.csv_path} already up to date ({result.total_rows} rows)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

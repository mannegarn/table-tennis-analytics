"""Sync senior WTT match-detail events into dbt/seeds/event_sponsors_manual.csv."""

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

DEFAULT_CSV_PATH = project_root() / "dbt" / "seeds" / "event_sponsors_manual.csv"

CSV_FIELDNAMES: tuple[str, ...] = (
    "event_id",
    "ball_sponsor",
    "table_sponsor",
    "start_date",
    "event_name",
)

# Senior Men's or Women's matches in stg_match_details (excludes U11..U21 youth
# codes and Mixed-only codes, matching docs/database-design.md §10.4).
_SENIOR_MW_DETAIL_QUERY = """
with senior_mw_events as (
    select distinct d.event_id
    from staging_wtt.stg_match_details as d
    where d.document_code ~ '^TTE[MW](?:SINGLES|DOUBLES|TEAM)'
      and d.document_code !~ '^TTE[MWX](?:SINGLES|DOUBLES|TEAM)-+U[0-9]{2}'
)
select
    e.source_event_id as event_id,
    e.start_date,
    e.event_name,
    (s.event_id is not null) as is_senior_mw_detail
from marts_wtt.dim_events as e
left join senior_mw_events as s
    on e.source_event_id = s.event_id
where s.event_id is not null
   or e.source_event_id = any(%s)
order by e.source_event_id asc
"""


@dataclass(frozen=True)
class EventMetadata:
    """Warehouse metadata for one event used to backfill/append CSV rows."""

    event_id: int
    start_date: date | None
    event_name: str
    is_senior_mw_detail: bool


@dataclass(frozen=True)
class SyncResult:
    """Summary of one manual-sponsors CSV sync."""

    csv_path: Path
    existing_rows: int
    added_rows: int
    backfilled_rows: int
    total_rows: int
    wrote_file: bool
    skipped_reason: str | None = None


def read_manual_sponsors_csv(csv_path: Path) -> list[dict[str, str]]:
    """Read existing rows from ``event_sponsors_manual.csv`` if present."""
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            {k: (v or "").strip() for k, v in row.items() if k is not None}
            for row in reader
            if (row.get("event_id") or "").strip()
        ]


def merge_manual_sponsor_rows(
    existing_rows: Sequence[Mapping[str, str]],
    warehouse_events: Iterable[EventMetadata],
) -> tuple[list[dict[str, str]], int, int]:
    """Merge existing manual rows with newly discovered senior M/W detail events.

    Rules:
    - Never drop an existing row from the CSV.
    - Never overwrite non-empty ``ball_sponsor``, ``table_sponsor``, ``start_date``,
      or ``event_name`` on existing rows.
    - Backfill blank ``start_date`` / ``event_name`` on existing rows from
      ``warehouse_events`` when available.
    - Append events with ``is_senior_mw_detail=True`` that are not yet in the CSV,
      with blank ``ball_sponsor`` and ``table_sponsor``.
    - Return rows sorted by ``event_id`` ascending, plus ``(added, backfilled)`` counts.
    """
    meta_by_id: dict[int, EventMetadata] = {ev.event_id: ev for ev in warehouse_events}
    merged: dict[int, dict[str, str]] = {}
    backfilled = 0

    for raw in existing_rows:
        event_id = int(raw["event_id"])
        meta = meta_by_id.get(event_id)

        existing_start = raw.get("start_date", "").strip()
        existing_name = raw.get("event_name", "").strip()

        start_date_str = existing_start
        if not start_date_str and meta is not None and meta.start_date is not None:
            start_date_str = meta.start_date.isoformat()

        event_name = existing_name
        if not event_name and meta is not None:
            event_name = meta.event_name

        if start_date_str != existing_start or event_name != existing_name:
            backfilled += 1

        merged[event_id] = {
            "event_id": str(event_id),
            "ball_sponsor": raw.get("ball_sponsor", "").strip(),
            "table_sponsor": raw.get("table_sponsor", "").strip(),
            "start_date": start_date_str,
            "event_name": event_name,
        }

    added = 0
    for event_id, meta in sorted(meta_by_id.items()):
        if not meta.is_senior_mw_detail or event_id in merged:
            continue
        merged[event_id] = {
            "event_id": str(event_id),
            "ball_sponsor": "",
            "table_sponsor": "",
            "start_date": meta.start_date.isoformat() if meta.start_date else "",
            "event_name": meta.event_name,
        }
        added += 1

    ordered_rows = [merged[eid] for eid in sorted(merged)]
    return ordered_rows, added, backfilled


def render_manual_sponsors_csv(rows: Sequence[Mapping[str, str]]) -> str:
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


def _required_tables_exist(conn: Connection[Any]) -> bool:
    """Return True when both dbt tables needed for sync exist."""
    row = conn.execute(
        """
        select
            to_regclass('marts_wtt.dim_events') is not null as has_dim_events,
            to_regclass('staging_wtt.stg_match_details') is not null as has_details
        """
    ).fetchone()
    if row is None:
        return False
    return bool(row["has_dim_events"] and row["has_details"])


def fetch_event_metadata(
    conn: Connection[Any],
    existing_event_ids: Sequence[int],
) -> list[EventMetadata] | None:
    """Fetch senior M/W detail events plus metadata for existing CSV event_ids.

    Returns ``None`` if the required dbt tables have not been built yet.
    """
    if not _required_tables_exist(conn):
        return None
    rows = conn.execute(
        _SENIOR_MW_DETAIL_QUERY,
        (list(existing_event_ids),),
    ).fetchall()
    return [
        EventMetadata(
            event_id=int(r["event_id"]),
            start_date=r["start_date"],
            event_name=str(r["event_name"] or ""),
            is_senior_mw_detail=bool(r["is_senior_mw_detail"]),
        )
        for r in rows
    ]


def sync_manual_sponsors_csv(
    csv_path: Path = DEFAULT_CSV_PATH,
    conn: Connection[Any] | None = None,
) -> SyncResult:
    """Sync senior M/W match-detail events into ``event_sponsors_manual.csv``."""
    existing_rows = read_manual_sponsors_csv(csv_path)
    existing_ids = [int(r["event_id"]) for r in existing_rows]

    if conn is None:
        with get_connection() as managed_conn:
            metadata = fetch_event_metadata(managed_conn, existing_ids)
    else:
        metadata = fetch_event_metadata(conn, existing_ids)

    if metadata is None:
        reason = "marts_wtt.dim_events or staging_wtt.stg_match_details not built yet"
        logger.warning("Skipping manual sponsors CSV sync: %s", reason)
        return SyncResult(
            csv_path=csv_path,
            existing_rows=len(existing_rows),
            added_rows=0,
            backfilled_rows=0,
            total_rows=len(existing_rows),
            wrote_file=False,
            skipped_reason=reason,
        )

    merged_rows, added, backfilled = merge_manual_sponsor_rows(existing_rows, metadata)
    rendered = render_manual_sponsors_csv(merged_rows)
    previous = csv_path.read_text(encoding="utf-8") if csv_path.exists() else ""

    if rendered == previous:
        logger.info(
            "event_sponsors_manual.csv already up to date (%d rows)",
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
            "Append senior Men's/Women's match-detail events to "
            "dbt/seeds/event_sponsors_manual.csv without overwriting manual edits."
        )
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help=f"Target manual sponsors CSV path (default: {DEFAULT_CSV_PATH})",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-event-sponsors-sync``."""
    configure_logging()
    args = _parse_args(argv)
    result = sync_manual_sponsors_csv(args.csv)
    if result.skipped_reason:
        print(f"Skipped manual sponsors sync: {result.skipped_reason}")  # noqa: T201
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

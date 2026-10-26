"""Inspect saved event-calendar payloads without a database.

The report answers three questions that were previously guesses:

* does ``Count`` match the number of rows actually saved?
* which field really carries the event tier?
* does the same ``EventId`` appear in more than one year?
"""

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from tt_stats.events.collect.events_by_year import (
    extract_counts,
    extract_rows,
)

DEFAULT_INPUT_DIR = Path("data/raw/events")


def inspect_file(path: Path) -> dict[str, Any]:
    """Summarise one saved payload."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = extract_rows(payload)
    api_count, original_count = extract_counts(payload)

    event_types = Counter(_label(row.get("EventType")) for row in rows)
    event_tiers = Counter(_label(row.get("Event_Tier_Name")) for row in rows)
    event_ids = [
        event_id for row in rows if isinstance(event_id := row.get("EventId"), int)
    ]

    return {
        "file": path.name,
        "year": _year_from_name(path),
        "api_count": api_count,
        "original_count": original_count,
        "row_count": len(rows),
        "count_matches": api_count == len(rows) if api_count is not None else None,
        "event_types": dict(event_types.most_common()),
        "event_tiers": dict(event_tiers.most_common()),
        "event_ids": event_ids,
    }


def inspect_directory(input_dir: Path) -> dict[str, Any]:
    """Summarise every payload in a directory and find cross-year duplicates."""
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for path in sorted(input_dir.glob("events_*.json")):
        if path.name.endswith(".meta.json"):
            continue
        try:
            records.append(inspect_file(path))
        except (OSError, ValueError) as error:
            errors.append({"file": path.name, "error": str(error)})

    years_by_id: dict[int, set[int]] = {}
    for record in records:
        year = record["year"]
        if not isinstance(year, int):
            continue
        for event_id in record["event_ids"]:
            years_by_id.setdefault(event_id, set()).add(year)

    duplicates = {
        str(event_id): sorted(years)
        for event_id, years in years_by_id.items()
        if len(years) > 1
    }
    count_mismatches = [
        {
            "year": record["year"],
            "api_count": record["api_count"],
            "row_count": record["row_count"],
        }
        for record in records
        if record["count_matches"] is False
    ]

    return {
        "files": records,
        "cross_year_duplicates": duplicates,
        "count_mismatches": count_mismatches,
        "errors": errors,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-events-inspect``."""
    parser = argparse.ArgumentParser(description="Inspect saved WTT event payloads.")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
    )
    args = parser.parse_args(argv)

    input_dir: Path = args.input_dir
    if not input_dir.exists():
        sys.stderr.write(f"Input directory does not exist: {input_dir}\n")
        return 1

    summary = inspect_directory(input_dir)
    sys.stdout.write(json.dumps(summary, indent=2, default=str) + "\n")
    return 0


def _label(value: object) -> str:
    return "<missing>" if value is None else str(value)


def _year_from_name(path: Path) -> int | None:
    suffix = path.stem.rsplit("_", maxsplit=1)[-1]
    return int(suffix) if suffix.isdigit() else None


if __name__ == "__main__":
    raise SystemExit(main())

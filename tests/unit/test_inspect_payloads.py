"""Unit tests for the saved-payload inspection report. No network."""

import json
from pathlib import Path
from typing import Any

from tt_stats.events.collect.inspect_payloads import inspect_directory


def _row(event_id: int, event_type: str, tier: str | None) -> dict[str, Any]:
    return {
        "EventId": event_id,
        "EventName": f"Event {event_id}",
        "EventType": event_type,
        "Event_Tier_Name": tier,
    }


def _write_payload(path: Path, rows: list[dict[str, Any]]) -> None:
    payload = [
        {
            "rows": rows,
            "Count": len(rows),
            "Original_Count": str(len(rows) + 2),
            "filterconfig": {},
        }
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_inspect_reports_tiers_types_and_cross_year_duplicates(
    tmp_path: Path,
) -> None:
    _write_payload(
        tmp_path / "events_2023.json",
        [_row(1001, "WTT Contender", "WTT Series")],
    )
    _write_payload(
        tmp_path / "events_2024.json",
        [
            _row(1001, "WTT Contender", "WTT Series"),
            _row(2002, "WTT Grand Smash", None),
        ],
    )

    summary = inspect_directory(tmp_path)

    assert summary["cross_year_duplicates"] == {"1001": [2023, 2024]}
    assert summary["count_mismatches"] == []
    assert summary["errors"] == []

    tiers_2024 = summary["files"][1]["event_tiers"]
    assert tiers_2024["WTT Series"] == 1
    assert tiers_2024["<missing>"] == 1


def test_inspect_reports_count_mismatches(tmp_path: Path) -> None:
    payload = [
        {
            "rows": [_row(1001, "WTT Contender", "WTT Series")],
            "Count": 2,
            "Original_Count": "3",
        }
    ]
    (tmp_path / "events_2023.json").write_text(json.dumps(payload), encoding="utf-8")

    summary = inspect_directory(tmp_path)

    assert summary["count_mismatches"] == [
        {"year": 2023, "api_count": 2, "row_count": 1}
    ]


def test_inspect_records_unreadable_files(tmp_path: Path) -> None:
    (tmp_path / "events_2023.json").write_text("not json", encoding="utf-8")

    summary = inspect_directory(tmp_path)

    assert summary["errors"]
    assert summary["errors"][0]["file"] == "events_2023.json"

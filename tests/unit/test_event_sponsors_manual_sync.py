"""Unit tests for syncing senior match-detail events into event_sponsors_manual.csv."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from tt_stats.event_sponsors.manual_sync import (
    EventMetadata,
    merge_manual_sponsor_rows,
    read_manual_sponsors_csv,
    render_manual_sponsors_csv,
)


def test_merge_preserves_manual_edits_and_backfills_start_date() -> None:
    existing = [
        {
            "event_id": "2411",
            "ball_sponsor": "DHS",
            "table_sponsor": "Double Fish",
            "event_name": "WTT Star Contender Doha 2021 (Mar 2021)",
        },
        {
            "event_id": "2860",
            "ball_sponsor": "Ownwin",
            "table_sponsor": "DHS",
            "event_name": "ITTF Mixed Team World Cup 2023",
            "table_sponsor_source": "gemini_youtube",
        },
    ]
    warehouse = [
        EventMetadata(
            event_id=2411,
            start_date=date(2021, 3, 8),
            event_name="WTT Star Contender Doha 2021",
            is_senior_mw_detail=True,
        ),
        EventMetadata(
            event_id=2860,
            start_date=date(2023, 12, 4),
            event_name="ITTF Mixed Team World Cup 2023",
            is_senior_mw_detail=False,
        ),
        EventMetadata(
            event_id=3256,
            start_date=date(2026, 11, 16),
            event_name="WTT Star Contender Muscat 2026",
            is_senior_mw_detail=True,
        ),
        EventMetadata(
            event_id=9999,
            start_date=date(2026, 5, 1),
            event_name="WTT Youth Contender Sample 2026",
            is_senior_mw_detail=False,
        ),
    ]

    rows, added, backfilled = merge_manual_sponsor_rows(existing, warehouse)

    assert added == 1
    assert backfilled == 2
    assert [r["event_id"] for r in rows] == ["2411", "2860", "3256"]

    # Manual event_name and sponsors on 2411 are preserved; start_date is backfilled.
    assert rows[0] == {
        "event_id": "2411",
        "ball_sponsor": "DHS",
        "table_sponsor": "Double Fish",
        "start_date": "2021-03-08",
        "event_name": "WTT Star Contender Doha 2021 (Mar 2021)",
        "table_sponsor_source": "",
    }
    # Existing non-senior-MW event (Mixed Team) and source tag are preserved.
    assert rows[1] == {
        "event_id": "2860",
        "ball_sponsor": "Ownwin",
        "table_sponsor": "DHS",
        "start_date": "2023-12-04",
        "event_name": "ITTF Mixed Team World Cup 2023",
        "table_sponsor_source": "gemini_youtube",
    }
    # New senior M/W detail event is appended with blank sponsors.
    assert rows[2] == {
        "event_id": "3256",
        "ball_sponsor": "",
        "table_sponsor": "",
        "start_date": "2026-11-16",
        "event_name": "WTT Star Contender Muscat 2026",
        "table_sponsor_source": "",
    }


def test_merge_is_idempotent_and_preserves_manual_start_date(tmp_path: Path) -> None:
    csv_path = tmp_path / "event_sponsors_manual.csv"
    warehouse = [
        EventMetadata(
            event_id=3256,
            start_date=date(2026, 11, 16),
            event_name="WTT Star Contender Muscat 2026",
            is_senior_mw_detail=True,
        )
    ]

    first_rows, added_1, backfilled_1 = merge_manual_sponsor_rows([], warehouse)
    assert (added_1, backfilled_1) == (1, 0)
    first_text = render_manual_sponsors_csv(first_rows)
    csv_path.write_text(first_text, encoding="utf-8")

    # Simulate the user filling in sponsors and tweaking the start date manually.
    edited_rows = read_manual_sponsors_csv(csv_path)
    edited_rows[0]["ball_sponsor"] = "DHS"
    edited_rows[0]["table_sponsor"] = "Double Fish"
    edited_rows[0]["start_date"] = "2026-11-15"
    edited_text = render_manual_sponsors_csv(edited_rows)
    csv_path.write_text(edited_text, encoding="utf-8")

    reloaded = read_manual_sponsors_csv(csv_path)
    second_rows, added_2, backfilled_2 = merge_manual_sponsor_rows(reloaded, warehouse)
    assert (added_2, backfilled_2) == (0, 0)
    assert render_manual_sponsors_csv(second_rows) == edited_text

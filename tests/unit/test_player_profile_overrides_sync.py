"""Unit tests for syncing missing profiles into player_profile_overrides.csv."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

from tt_stats.players.profile_overrides_sync import (
    PlayerProfileMetadata,
    merge_profile_override_rows,
    read_profile_overrides_csv,
    render_profile_overrides_csv,
    sync_profile_overrides_csv,
)


def test_merge_preserves_manual_edits_and_backfills_name_and_dob() -> None:
    existing = [
        {
            "ittf_id": "100001",
            "player_name": "",
            "dob": "",
            "hand": "Right",
            "grip": "Shakehand",
            "style": "Attacker",
            "notes": "Verified from WTT bio",
        },
        {
            "ittf_id": "100002",
            "player_name": "Existing Name",
            "dob": "1995-05-10",
            "hand": "Left",
            "grip": "",
            "style": "",
            "notes": "",
        },
    ]
    warehouse = [
        PlayerProfileMetadata(
            ittf_id="100001",
            player_name="Alex MORGAN",
            dob=date(1990, 1, 15),
            hand="Right",
            grip="Shakehand",
            style="Attacker",
            is_missing_profile=False,
        ),
        PlayerProfileMetadata(
            ittf_id="100002",
            player_name="Warehouse Name",
            dob=date(1995, 5, 10),
            hand="Right",
            grip="Shakehand",
            style=None,
            is_missing_profile=True,
        ),
        PlayerProfileMetadata(
            ittf_id="200500",
            player_name="Jane DOE",
            dob=date(2002, 11, 3),
            hand="Right",
            grip=None,
            style=None,
            is_missing_profile=True,
        ),
        PlayerProfileMetadata(
            ittf_id="99999",
            player_name="Bob SMITH",
            dob=None,
            hand=None,
            grip="Penhold",
            style="Attacker",
            is_missing_profile=True,
        ),
    ]

    rows, added, backfilled = merge_profile_override_rows(existing, warehouse)

    assert added == 2
    assert backfilled == 1
    assert [r["ittf_id"] for r in rows] == ["99999", "100001", "100002", "200500"]

    assert rows[0] == {
        "ittf_id": "99999",
        "player_name": "Bob SMITH",
        "dob": "",
        "hand": "",
        "grip": "Penhold",
        "style": "Attacker",
        "notes": "",
    }
    assert rows[1] == {
        "ittf_id": "100001",
        "player_name": "Alex MORGAN",
        "dob": "1990-01-15",
        "hand": "Right",
        "grip": "Shakehand",
        "style": "Attacker",
        "notes": "Verified from WTT bio",
    }
    assert rows[2] == {
        "ittf_id": "100002",
        "player_name": "Existing Name",
        "dob": "1995-05-10",
        "hand": "Left",
        "grip": "",
        "style": "",
        "notes": "",
    }
    assert rows[3] == {
        "ittf_id": "200500",
        "player_name": "Jane DOE",
        "dob": "2002-11-03",
        "hand": "Right",
        "grip": "",
        "style": "",
        "notes": "",
    }


def test_render_and_read_roundtrip(tmp_path: Path) -> None:
    rows = [
        {
            "ittf_id": "100001",
            "player_name": "Alex MORGAN",
            "dob": "1990-01-15",
            "hand": "Right",
            "grip": "Shakehand",
            "style": "Attacker",
            "notes": "",
        }
    ]
    csv_path = tmp_path / "player_profile_overrides.csv"
    csv_path.write_text(render_profile_overrides_csv(rows), encoding="utf-8")

    assert read_profile_overrides_csv(csv_path) == rows


def test_sync_skips_when_dim_players_missing(tmp_path: Path) -> None:
    csv_path = tmp_path / "player_profile_overrides.csv"
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = {"has_dim_players": False}

    result = sync_profile_overrides_csv(csv_path=csv_path, conn=mock_conn)

    assert result.wrote_file is False
    assert result.skipped_reason == "marts_wtt.dim_players not built yet"
    assert not csv_path.exists()

"""Integration tests: seeding ingest.ittf_player_profiles from a local CSV."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from psycopg import Connection

from tt_stats.players.land.ittf_seed import seed_ittf_player_profiles

pytestmark = pytest.mark.integration


def test_seed_ittf_player_profiles_loads_and_preserves_live_rows(
    db_connection: Connection[Any],
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "master_players.csv"
    csv_path.write_text(
        "\n".join(
            [
                "playerId,PlayerName,Style,Grip,Hand",
                "90051,Francisco LOPEZ,Attacker,Shakehand,Right",
                "90052,Jane DOE,,,Left",
                "90053,Live SCRAPED,Defender,Penhold,Right",
            ]
        ),
        encoding="utf-8",
    )

    # Pre-existing live-scraped row should not be overwritten by the CSV seed.
    db_connection.execute(
        """
        INSERT INTO ingest.ittf_player_profiles (
            player_id, style, grip, hand, source, http_status, fetched_at
        )
        VALUES (90053, 'LiveStyle', 'Penhold', 'Left', 'ittf_results_link', 200, %s);
        """,
        (datetime.now(tz=UTC),),
    )

    seeded = seed_ittf_player_profiles(db_connection, csv_path)
    assert seeded == 3

    # Re-running is idempotent.
    assert seed_ittf_player_profiles(db_connection, csv_path) == 3

    rows = db_connection.execute(
        """
        SELECT player_id, style, grip, hand, source
        FROM ingest.ittf_player_profiles
        ORDER BY player_id;
        """
    ).fetchall()
    assert rows == [
        {
            "player_id": 90051,
            "style": "Attacker",
            "grip": "Shakehand",
            "hand": "Right",
            "source": "master_players.csv",
        },
        {
            "player_id": 90052,
            "style": None,
            "grip": None,
            "hand": "Left",
            "source": "master_players.csv",
        },
        {
            "player_id": 90053,
            "style": "LiveStyle",
            "grip": "Penhold",
            "hand": "Left",
            "source": "ittf_results_link",
        },
    ]

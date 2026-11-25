"""Unit tests and privacy guardrails for the Streamlit Parquet export."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import polars as pl
import pytest

from tt_stats.analytics.export_app_data import (
    COMMENTARY_EVENTS_SQL,
    EXPORT_QUERIES,
    LAST_COLLECTED_SQL,
    MARTS_BUILT_SQL,
    SERVE_RECEIVE_FILENAME,
    build_player_serve_receive_stats,
    default_output_dir,
    export_all,
    resolve_commentary_points,
)
from tt_stats.core.config import Settings, project_root


def test_dim_players_export_query_uses_age_and_filters_senior_singles() -> None:
    queries_by_file = {q.filename: q.sql.lower() for q in EXPORT_QUERIES}
    sql = queries_by_file["dim_players.parquet"]

    assert "marts.fct_senior_singles_matches" in sql
    assert "end as age," in sql
    # Raw dob must never be projected directly as a column.
    projected_lines = [line.strip().rstrip(",") for line in sql.splitlines()]
    assert "dob" not in projected_lines
    assert "p.dob" not in projected_lines


def test_exported_dim_players_parquet_has_no_dob_and_only_senior_players() -> None:
    data_dir = default_output_dir()
    players_path = data_dir / "dim_players.parquet"
    matches_path = data_dir / "fct_senior_singles_matches.parquet"

    assert players_path.exists()
    assert matches_path.exists()

    players = pl.read_parquet(players_path)
    matches = pl.read_parquet(matches_path)

    assert "dob" not in players.columns
    assert "age" in players.columns
    assert players.height > 0

    active_player_sks = (
        pl.concat(
            [
                matches.select(pl.col("p1_player_sk").alias("player_sk")),
                matches.select(pl.col("p2_player_sk").alias("player_sk")),
            ]
        )
        .drop_nulls()
        .unique()
    )
    unreferenced = players.join(active_player_sks, on="player_sk", how="anti")
    assert unreferenced.is_empty()

    ages = players["age"].drop_nulls()
    assert ages.len() > 0
    min_age = ages.min()
    max_age = ages.max()
    assert isinstance(min_age, int)
    assert min_age >= 0
    assert isinstance(max_age, int)
    assert max_age <= 120


def test_gitignore_excludes_dbt_seed_csvs() -> None:
    gitignore = (project_root() / ".gitignore").read_text(encoding="utf-8")
    lines = [line.strip() for line in gitignore.splitlines()]
    assert "*.csv" in lines
    assert "!dbt/seeds/*.csv" not in lines


def _default_dim_players_rows() -> list[dict[str, Any]]:
    return [
        {
            "player_sk": 1,
            "player_name": "Alice CHEN",
            "organization_code": "CHN",
            "gender": "F",
            "hand": None,
            "grip": "Shakehand",
            "style": None,
        },
        {
            "player_sk": 2,
            "player_name": "Bob MULLER",
            "organization_code": "GER",
            "gender": "M",
            "hand": "Right",
            "grip": None,
            "style": "Attacker",
        },
    ]


def _default_commentary_rows() -> list[dict[str, Any]]:
    return [
        {
            "match_sk": 100,
            "sequence_number": 1,
            "action_type": "PT",
            "p1_player_sk": 1,
            "p2_player_sk": 2,
            "actor_side": 1,
        },
        {
            "match_sk": 100,
            "sequence_number": 2,
            "action_type": "SSR",
            "p1_player_sk": 1,
            "p2_player_sk": 2,
            "actor_side": 2,
        },
        {
            "match_sk": 100,
            "sequence_number": 3,
            "action_type": "PT",
            "p1_player_sk": 1,
            "p2_player_sk": 2,
            "actor_side": 2,
        },
    ]


def _mock_conn(
    query_rows: dict[str, list[dict[str, Any]]] | None = None,
    *,
    fail_on_filename: str | None = None,
    marts_built_at: datetime | None = datetime(2026, 11, 25, 13, 0, tzinfo=UTC),
    last_collected_at: datetime | None = datetime(2026, 11, 25, 12, 56, tzinfo=UTC),
) -> MagicMock:
    rows_by_sql: dict[str, list[dict[str, Any]]] = {}
    for q in EXPORT_QUERIES:
        if query_rows and q.filename in query_rows:
            rows_by_sql[q.sql] = query_rows[q.filename]
        elif q.filename == "dim_players.parquet":
            rows_by_sql[q.sql] = _default_dim_players_rows()
        else:
            rows_by_sql[q.sql] = [{"col": 1}]
    rows_by_sql[COMMENTARY_EVENTS_SQL] = (
        query_rows[SERVE_RECEIVE_FILENAME]
        if query_rows and SERVE_RECEIVE_FILENAME in query_rows
        else _default_commentary_rows()
    )

    fail_sql = (
        next(q.sql for q in EXPORT_QUERIES if q.filename == fail_on_filename)
        if fail_on_filename
        else None
    )

    def execute(sql: str) -> MagicMock:
        cursor = MagicMock()
        if fail_sql is not None and sql == fail_sql:
            raise RuntimeError("boom")
        if sql in rows_by_sql:
            cursor.fetchall.return_value = rows_by_sql[sql]
        elif "to_regclass('marts.mart_metadata')" in sql:
            cursor.fetchone.return_value = {"exists": marts_built_at is not None}
        elif sql == MARTS_BUILT_SQL:
            cursor.fetchone.return_value = {"built_at_utc": marts_built_at}
        elif sql == LAST_COLLECTED_SQL:
            cursor.fetchone.return_value = {"last_collected_at": last_collected_at}
        return cursor

    conn = MagicMock()
    conn.execute.side_effect = execute
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    return conn


def _dummy_settings() -> Settings:
    return Settings(postgres_password="test")  # noqa: S106


def test_export_all_writes_parquets_and_marts_built_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_dir = tmp_path / "data"
    conn = _mock_conn()
    monkeypatch.setattr(
        "tt_stats.analytics.export_app_data.psycopg.connect",
        lambda *_a, **_kw: conn,
    )

    counts = export_all(_dummy_settings(), output_dir=out_dir)

    expected_files = {q.filename for q in EXPORT_QUERIES} | {SERVE_RECEIVE_FILENAME}
    assert set(counts) == expected_files
    assert counts[SERVE_RECEIVE_FILENAME] == 2
    for fname in expected_files:
        assert (out_dir / fname).exists()

    meta = json.loads((out_dir / "snapshot_metadata.json").read_text(encoding="utf-8"))
    assert meta["marts_built_at_utc"] == "2026-11-25 13:00 UTC"
    assert meta["last_collected_at_utc"] == "2026-11-25 12:56 UTC"
    assert meta["exported_at_utc"]
    assert list(tmp_path.iterdir()) == [out_dir]


def test_export_all_refuses_empty_frame_and_preserves_existing_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_dir = tmp_path / "data"
    out_dir.mkdir()
    existing = out_dir / "dim_events.parquet"
    existing.write_bytes(b"original-snapshot")

    conn = _mock_conn(query_rows={"dim_players.parquet": []})
    monkeypatch.setattr(
        "tt_stats.analytics.export_app_data.psycopg.connect",
        lambda *_a, **_kw: conn,
    )

    with pytest.raises(RuntimeError, match="returned 0 rows"):
        export_all(_dummy_settings(), output_dir=out_dir)

    assert existing.read_bytes() == b"original-snapshot"
    assert not (out_dir / "snapshot_metadata.json").exists()
    assert list(tmp_path.iterdir()) == [out_dir]


def test_export_all_is_atomic_on_mid_export_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_dir = tmp_path / "data"
    out_dir.mkdir()
    existing_events = out_dir / "dim_events.parquet"
    existing_events.write_bytes(b"old-events")

    conn = _mock_conn(fail_on_filename="fct_senior_singles_matches.parquet")
    monkeypatch.setattr(
        "tt_stats.analytics.export_app_data.psycopg.connect",
        lambda *_a, **_kw: conn,
    )

    with pytest.raises(RuntimeError, match="boom"):
        export_all(_dummy_settings(), output_dir=out_dir)

    assert existing_events.read_bytes() == b"old-events"
    assert not (out_dir / "dim_players.parquet").exists()
    assert list(tmp_path.iterdir()) == [out_dir]


def test_resolve_commentary_points_infers_server_and_handles_dpt_and_unknown() -> None:
    events = pl.DataFrame(
        [
            # Match 1: opens with unknown SSR before first known SSR (p2=2),
            # so unknown_ssr_before == 1 (odd) -> opening server is 2; after unknown
            # SSR it flips to 1; after SSR=2 it becomes 2. Also tests DPT canceling PT.
            {
                "match_sk": 1,
                "sequence_number": 1,
                "action_type": "SSR",
                "p1_player_sk": 10,
                "p2_player_sk": 20,
                "actor_side": 0,
            },
            {
                "match_sk": 1,
                "sequence_number": 2,
                "action_type": "PT",
                "p1_player_sk": 10,
                "p2_player_sk": 20,
                "actor_side": 1,
            },
            {
                "match_sk": 1,
                "sequence_number": 3,
                "action_type": "SSR",
                "p1_player_sk": 10,
                "p2_player_sk": 20,
                "actor_side": 2,
            },
            {
                "match_sk": 1,
                "sequence_number": 4,
                "action_type": "PT",
                "p1_player_sk": 10,
                "p2_player_sk": 20,
                "actor_side": 2,
            },
            {
                "match_sk": 1,
                "sequence_number": 5,
                "action_type": "PT",
                "p1_player_sk": 10,
                "p2_player_sk": 20,
                "actor_side": 1,
            },
            {
                "match_sk": 1,
                "sequence_number": 6,
                "action_type": "DPT",
                "p1_player_sk": 10,
                "p2_player_sk": 20,
                "actor_side": None,
            },
            {
                "match_sk": 1,
                "sequence_number": 7,
                "action_type": "SSR",
                "p1_player_sk": 10,
                "p2_player_sk": 20,
                "actor_side": 0,
            },
            {
                "match_sk": 1,
                "sequence_number": 8,
                "action_type": "PT",
                "p1_player_sk": 10,
                "p2_player_sk": 20,
                "actor_side": 1,
            },
            # Match 2: starts directly with PT before first known SSR(2) -> server 1
            {
                "match_sk": 2,
                "sequence_number": 1,
                "action_type": "PT",
                "p1_player_sk": 10,
                "p2_player_sk": 20,
                "actor_side": 1,
            },
            {
                "match_sk": 2,
                "sequence_number": 2,
                "action_type": "SSR",
                "p1_player_sk": 10,
                "p2_player_sk": 20,
                "actor_side": 2,
            },
            {
                "match_sk": 2,
                "sequence_number": 3,
                "action_type": "PT",
                "p1_player_sk": 10,
                "p2_player_sk": 20,
                "actor_side": 2,
            },
            # Match 3: no known SSR at all -> skipped
            {
                "match_sk": 3,
                "sequence_number": 1,
                "action_type": "PT",
                "p1_player_sk": 10,
                "p2_player_sk": 20,
                "actor_side": 1,
            },
        ],
        schema={
            "match_sk": pl.Int64,
            "sequence_number": pl.Int32,
            "action_type": pl.String,
            "p1_player_sk": pl.Int64,
            "p2_player_sk": pl.Int64,
            "actor_side": pl.Int8,
        },
    )

    pts = resolve_commentary_points(events)
    assert pts.height == 5
    assert pts["match_sk"].to_list() == [1, 1, 1, 2, 2]
    # Match 1: seq 2 (server=1, won=1), seq 4 (server=2, won=2), seq 8 (server=1, won=1)
    # Match 2: seq 1 (server=1, won=1), seq 3 (server=2, won=2)
    assert pts["server_side"].to_list() == [1, 2, 1, 1, 2]
    assert pts["actor_side"].to_list() == [1, 2, 1, 1, 2]

    players = pl.DataFrame(
        {
            "player_sk": [10, 20],
            "player_name": ["Player One", "Player Two"],
            "organization_code": ["JPN", None],
            "gender": ["M", "M"],
            "style": [None, "Defender"],
            "grip": ["", "Shakehand"],
            "hand": ["Right", None],
        }
    )
    agg = build_player_serve_receive_stats(events, players)
    p1 = agg.filter(pl.col("player_sk") == 10).row(0, named=True)
    assert p1["Matches"] == 2
    assert p1["Points Played"] == 5
    assert p1["Points Won"] == 3
    assert p1["Points Lost"] == 2
    assert p1["Serve Points"] == 3
    assert p1["Serve Won"] == 3
    assert p1["Serve Lost"] == 0
    assert p1["Receive Points"] == 2
    assert p1["Receive Won"] == 0
    assert p1["Receive Lost"] == 2
    assert p1["Style"] == "Unknown"
    assert p1["Grip"] == "Unknown"
    assert p1["Gender"] == "Men"

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
    EXPORT_QUERIES,
    LAST_COLLECTED_SQL,
    MARTS_BUILT_SQL,
    default_output_dir,
    export_all,
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


def _mock_conn(
    query_rows: dict[str, list[dict[str, Any]]] | None = None,
    *,
    fail_on_filename: str | None = None,
    marts_built_at: datetime | None = datetime(2026, 11, 25, 13, 0, tzinfo=UTC),
    last_collected_at: datetime | None = datetime(2026, 11, 25, 12, 56, tzinfo=UTC),
) -> MagicMock:
    rows_by_sql = {
        q.sql: (
            query_rows[q.filename]
            if query_rows and q.filename in query_rows
            else [{"col": 1}]
        )
        for q in EXPORT_QUERIES
    }
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

    assert set(counts) == {q.filename for q in EXPORT_QUERIES}
    assert all(count == 1 for count in counts.values())
    for q in EXPORT_QUERIES:
        assert (out_dir / q.filename).exists()

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

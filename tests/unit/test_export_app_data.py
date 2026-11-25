"""Unit tests and privacy guardrails for the Streamlit Parquet export."""

from __future__ import annotations

import polars as pl

from tt_stats.analytics.export_app_data import EXPORT_QUERIES, default_output_dir
from tt_stats.core.config import project_root


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

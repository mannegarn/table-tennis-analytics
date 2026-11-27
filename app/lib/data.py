"""Data loading and summary calculations from local Parquet snapshots."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import streamlit as st

# app/lib/data.py -> parents[1] is app/
APP_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = APP_DIR / "data"


@dataclass(frozen=True)
class EventBoundary:
    """Earliest or latest event that contains at least one match."""

    event_name: str
    start_date: date | None
    end_date: date | None
    country: str | None
    match_count: int


@dataclass(frozen=True)
class SummaryStats:
    """Pre-computed summary metrics displayed on the Summary page."""

    # Events
    total_events: int
    events_with_senior_singles: int
    events_with_catalog_matches: int
    catalog_first_event_date: date | None
    catalog_last_event_date: date | None

    # Senior singles matches
    senior_total_matches: int
    senior_mens_matches: int
    senior_womens_matches: int
    senior_bo5_matches: int
    senior_bo7_matches: int
    senior_other_format_matches: int

    # Full match catalog (all disciplines, youth, para, team rubbers)
    catalog_total_matches: int

    # Players
    total_players: int
    mens_players: int
    womens_players: int
    distinct_countries: int
    players_with_age: int
    min_age: int | None
    max_age: int | None

    # First & last events with matches (joined to dim_events)
    first_senior_event: EventBoundary | None
    last_senior_event: EventBoundary | None
    first_catalog_event: EventBoundary | None
    last_catalog_event: EventBoundary | None


@dataclass(frozen=True)
class HomeInsights:
    """Dynamic headline scale & discovery metrics rendered on the Home page."""

    senior_total_matches: int
    total_players: int
    distinct_countries: int
    events_with_senior_singles: int
    senior_year_min: int | None
    senior_year_max: int | None
    commentary_matches: int
    commentary_points: int
    global_serve_win_pct: float | None
    global_receive_win_pct: float | None
    server_edge_pp: float | None
    bo5_matches: int
    bo5_won_below_50: int
    bo5_won_below_50_pct: float | None
    defender_set_delta_pp: float | None
    defender_bo5_delta_pp: float | None
    defender_bo7_delta_pp: float | None


def data_files_exist() -> bool:
    """Return True when all required Parquet snapshots are present."""
    required = (
        "dim_events.parquet",
        "dim_players.parquet",
        "fct_senior_singles_matches.parquet",
        "fct_senior_singles_games.parquet",
        "catalog_matches_by_event_gender.parquet",
        "player_serve_receive_stats.parquet",
        "fct_glicko_match_ratings.parquet",
        "dim_table_brands.parquet",
    )
    return all((DATA_DIR / name).exists() for name in required)


def _file_version(path: Path) -> tuple[int, int] | None:
    """Return ``(mtime_ns, size)`` to bust ``st.cache_data`` when a file changes."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


@st.cache_data(show_spinner=False)
def _load_snapshot_timestamp_cached(
    meta_version: tuple[int, int] | None,
    events_version: tuple[int, int] | None,
) -> str | None:
    _ = (meta_version, events_version)
    meta_path = DATA_DIR / "snapshot_metadata.json"
    if meta_path.exists():
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            ts = (
                payload.get("marts_built_at_utc")
                or payload.get("last_collected_at_utc")
                or payload.get("exported_at_utc")
            )
            if isinstance(ts, str) and ts:
                return ts
        except (OSError, json.JSONDecodeError):
            pass

    events_path = DATA_DIR / "dim_events.parquet"
    if events_path.exists():
        mtime = datetime.fromtimestamp(events_path.stat().st_mtime, tz=UTC)
        return mtime.strftime("%Y-%m-%d %H:%M UTC")
    return None


def load_snapshot_timestamp() -> str | None:
    """Return the UTC timestamp of the last successful data collection."""
    return _load_snapshot_timestamp_cached(
        _file_version(DATA_DIR / "snapshot_metadata.json"),
        _file_version(DATA_DIR / "dim_events.parquet"),
    )


@st.cache_data(show_spinner=False)
def _read_parquet_cached(
    filename: str,
    version: tuple[int, int] | None,
) -> pl.DataFrame:
    _ = version
    return pl.read_parquet(DATA_DIR / filename)


def load_events() -> pl.DataFrame:
    """Load the events dimension from Parquet."""
    filename = "dim_events.parquet"
    return _read_parquet_cached(filename, _file_version(DATA_DIR / filename))


def load_players() -> pl.DataFrame:
    """Load the players dimension from Parquet."""
    filename = "dim_players.parquet"
    return _read_parquet_cached(filename, _file_version(DATA_DIR / filename))


def load_senior_matches() -> pl.DataFrame:
    """Load senior singles matches from Parquet."""
    filename = "fct_senior_singles_matches.parquet"
    return _read_parquet_cached(filename, _file_version(DATA_DIR / filename))


def load_senior_games() -> pl.DataFrame:
    """Load senior singles games (individual 11-point sets) from Parquet."""
    filename = "fct_senior_singles_games.parquet"
    return _read_parquet_cached(filename, _file_version(DATA_DIR / filename))


def load_catalog_rollup() -> pl.DataFrame:
    """Load pre-aggregated catalog match counts per event and gender."""
    filename = "catalog_matches_by_event_gender.parquet"
    return _read_parquet_cached(filename, _file_version(DATA_DIR / filename))


def load_serve_receive_stats() -> pl.DataFrame:
    """Load pre-aggregated per-player serve and receive stats from Parquet."""
    filename = "player_serve_receive_stats.parquet"
    return _read_parquet_cached(filename, _file_version(DATA_DIR / filename))


def load_glicko_match_ratings() -> pl.DataFrame:
    """Load pre- and post-match Glicko-2 ratings from Parquet."""
    filename = "fct_glicko_match_ratings.parquet"
    return _read_parquet_cached(filename, _file_version(DATA_DIR / filename))


def load_table_brands() -> pl.DataFrame:
    """Load table sponsor to table-group mappings from Parquet."""
    filename = "dim_table_brands.parquet"
    return _read_parquet_cached(filename, _file_version(DATA_DIR / filename))


def _pick_boundary_event(
    events: pl.DataFrame,
    counts_per_event: pl.DataFrame,
    *,
    descending: bool,
) -> EventBoundary | None:
    """Join per-event match counts to dim_events and return the first or last."""
    joined = (
        counts_per_event.join(
            events.select(
                "event_sk", "event_name", "start_date", "end_date", "country"
            ),
            on="event_sk",
            how="inner",
        )
        .filter(pl.col("start_date").is_not_null())
        .sort(["start_date", "event_sk"], descending=[descending, descending])
    )
    if joined.is_empty():
        return None
    row = joined.row(0, named=True)
    return EventBoundary(
        event_name=str(row["event_name"] or "Unknown Event"),
        start_date=row["start_date"],
        end_date=row["end_date"],
        country=row["country"],
        match_count=int(row["match_count"]),
    )


def compute_summary_stats(
    events: pl.DataFrame,
    players: pl.DataFrame,
    senior_matches: pl.DataFrame,
    catalog_rollup: pl.DataFrame,
) -> SummaryStats:
    """Compute all warehouse summary metrics from the loaded DataFrames."""
    # --- Events ---
    valid_event_dates = events.filter(pl.col("start_date").is_not_null())
    catalog_first_date: date | None = (
        valid_event_dates["start_date"].min()  # type: ignore[assignment]
        if not valid_event_dates.is_empty()
        else None
    )
    catalog_last_date: date | None = (
        valid_event_dates["start_date"].max()  # type: ignore[assignment]
        if not valid_event_dates.is_empty()
        else None
    )

    senior_per_event = senior_matches.group_by("event_sk").agg(
        pl.len().alias("match_count")
    )
    catalog_per_event = catalog_rollup.group_by("event_sk").agg(
        pl.col("match_count").sum().alias("match_count")
    )

    # --- Senior singles matches ---
    senior_total = senior_matches.height
    senior_mens = senior_matches.filter(pl.col("gender_category") == "men").height
    senior_womens = senior_matches.filter(pl.col("gender_category") == "women").height
    senior_bo5 = senior_matches.filter(pl.col("best_of") == 5).height
    senior_bo7 = senior_matches.filter(pl.col("best_of") == 7).height
    senior_other_fmt = senior_total - senior_bo5 - senior_bo7

    catalog_total = (
        int(catalog_rollup["match_count"].sum()) if not catalog_rollup.is_empty() else 0
    )

    # --- Players (filtered to players appearing in senior singles matches) ---
    active_player_sks = (
        pl.concat(
            [
                senior_matches.select(pl.col("p1_player_sk").alias("player_sk")),
                senior_matches.select(pl.col("p2_player_sk").alias("player_sk")),
            ]
        )
        .filter(pl.col("player_sk").is_not_null())
        .unique()
    )
    senior_players = players.join(active_player_sks, on="player_sk", how="semi")

    mens_players = senior_players.filter(pl.col("gender") == "M").height
    womens_players = senior_players.filter(pl.col("gender") == "F").height

    country_series = senior_players.select(
        pl.coalesce(["country_code", "organization_code"]).alias("country")
    ).filter(pl.col("country").is_not_null() & (pl.col("country") != ""))
    distinct_countries = int(country_series["country"].n_unique())

    # Guard against implausible ages when computing age range
    valid_ages = senior_players.filter(
        pl.col("age").is_not_null() & (pl.col("age") >= 0) & (pl.col("age") <= 120)
    )
    players_with_age = valid_ages.height
    min_age_raw = valid_ages["age"].min() if players_with_age > 0 else None
    max_age_raw = valid_ages["age"].max() if players_with_age > 0 else None
    min_age = int(min_age_raw) if isinstance(min_age_raw, int) else None
    max_age = int(max_age_raw) if isinstance(max_age_raw, int) else None

    return SummaryStats(
        total_events=events.height,
        events_with_senior_singles=senior_per_event.height,
        events_with_catalog_matches=catalog_per_event.height,
        catalog_first_event_date=catalog_first_date,
        catalog_last_event_date=catalog_last_date,
        senior_total_matches=senior_total,
        senior_mens_matches=senior_mens,
        senior_womens_matches=senior_womens,
        senior_bo5_matches=senior_bo5,
        senior_bo7_matches=senior_bo7,
        senior_other_format_matches=senior_other_fmt,
        catalog_total_matches=catalog_total,
        total_players=senior_players.height,
        mens_players=mens_players,
        womens_players=womens_players,
        distinct_countries=distinct_countries,
        players_with_age=players_with_age,
        min_age=min_age,
        max_age=max_age,
        first_senior_event=_pick_boundary_event(
            events, senior_per_event, descending=False
        ),
        last_senior_event=_pick_boundary_event(
            events, senior_per_event, descending=True
        ),
        first_catalog_event=_pick_boundary_event(
            events, catalog_per_event, descending=False
        ),
        last_catalog_event=_pick_boundary_event(
            events, catalog_per_event, descending=True
        ),
    )


def build_yearly_matches_table(
    events: pl.DataFrame,
    senior_matches: pl.DataFrame,
) -> pl.DataFrame:
    """Join senior singles matches to events and aggregate by year and gender."""
    return (
        senior_matches.join(
            events.select("event_sk", "event_year"),
            on="event_sk",
            how="inner",
        )
        .filter(pl.col("event_year").is_not_null())
        .with_columns(
            pl.when(pl.col("gender_category") == "men")
            .then(pl.lit("Men"))
            .when(pl.col("gender_category") == "women")
            .then(pl.lit("Women"))
            .otherwise(pl.lit("Other / Mixed"))
            .alias("Gender")
        )
        .group_by(["event_year", "Gender"])
        .agg(pl.len().alias("Matches"))
        .sort(["event_year", "Gender"])
    )


def _mean_defender_delta_pp(
    df: pl.DataFrame,
    *,
    mode: str,
    best_of: int | None = None,
    min_matches: int = 10,
) -> float | None:
    """Return mean percentage-point delta vs binomial curve for Defenders."""
    import numpy as np

    from .win_rates import PlotMode, compute_binomial_expected, filter_win_rate_rows

    filtered = filter_win_rate_rows(
        df,
        min_matches=min_matches,
        best_of=best_of,
        styles=["Defender"],
    )
    if filtered.is_empty():
        return None
    x_col = "Point %"
    y_col = "Set %" if mode == "point_to_set" else "Win %"
    clean = filtered.filter(
        pl.col(x_col).is_not_null()
        & pl.col(y_col).is_not_null()
        & pl.col(x_col).is_finite()
        & pl.col(y_col).is_finite()
    )
    if clean.is_empty():
        return None
    xs = clean[x_col].to_numpy().astype(np.float64)
    ys = clean[y_col].to_numpy().astype(np.float64)
    plot_mode: PlotMode = "point_to_set" if mode == "point_to_set" else "point_to_match"
    expected = compute_binomial_expected(xs, mode=plot_mode, best_of=best_of)
    return round(float(np.mean(ys - expected)), 2)


def compute_home_insights(
    events: pl.DataFrame,
    players: pl.DataFrame,
    senior_matches: pl.DataFrame,
    senior_games: pl.DataFrame,
    catalog_rollup: pl.DataFrame,
    serve_receive_raw: pl.DataFrame,
) -> HomeInsights:
    """Compute live scale and headline analytics insights for the Home page."""
    from .serve_receive import compute_global_kpis, enrich_serve_receive_stats
    from .win_rates import (
        build_combined_point_set_stats,
        build_format_win_rates,
        build_match_point_share_bins,
    )

    summary = compute_summary_stats(events, players, senior_matches, catalog_rollup)

    yearly = build_yearly_matches_table(events, senior_matches)
    if not yearly.is_empty():
        yr_min_raw = yearly["event_year"].min()
        yr_max_raw = yearly["event_year"].max()
        senior_year_min = int(yr_min_raw) if isinstance(yr_min_raw, int) else None
        senior_year_max = int(yr_max_raw) if isinstance(yr_max_raw, int) else None
    else:
        senior_year_min = None
        senior_year_max = None

    sr_enriched = enrich_serve_receive_stats(serve_receive_raw)
    sr_kpis = compute_global_kpis(sr_enriched)
    server_edge_pp: float | None = None
    if (
        sr_kpis.global_serve_win_pct is not None
        and sr_kpis.global_receive_win_pct is not None
    ):
        server_edge_pp = round(
            sr_kpis.global_serve_win_pct - sr_kpis.global_receive_win_pct, 2
        )

    _, bo5_matches, bo5_below_50, _ = build_match_point_share_bins(
        senior_matches,
        best_of=5,
        gender="All",
    )
    bo5_below_50_pct = (
        round(100.0 * bo5_below_50 / bo5_matches, 2) if bo5_matches > 0 else None
    )

    combined_df = build_combined_point_set_stats(senior_matches, senior_games, players)
    format_df = build_format_win_rates(senior_matches, senior_games, players)

    return HomeInsights(
        senior_total_matches=summary.senior_total_matches,
        total_players=summary.total_players,
        distinct_countries=summary.distinct_countries,
        events_with_senior_singles=summary.events_with_senior_singles,
        senior_year_min=senior_year_min,
        senior_year_max=senior_year_max,
        commentary_matches=sr_kpis.total_matches,
        commentary_points=sr_kpis.total_points,
        global_serve_win_pct=sr_kpis.global_serve_win_pct,
        global_receive_win_pct=sr_kpis.global_receive_win_pct,
        server_edge_pp=server_edge_pp,
        bo5_matches=bo5_matches,
        bo5_won_below_50=bo5_below_50,
        bo5_won_below_50_pct=bo5_below_50_pct,
        defender_set_delta_pp=_mean_defender_delta_pp(
            combined_df, mode="point_to_set", best_of=None
        ),
        defender_bo5_delta_pp=_mean_defender_delta_pp(
            format_df, mode="point_to_match", best_of=5
        ),
        defender_bo7_delta_pp=_mean_defender_delta_pp(
            format_df, mode="point_to_match", best_of=7
        ),
    )

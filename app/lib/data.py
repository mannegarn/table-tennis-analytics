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
    players_with_dob: int
    min_age: int | None
    max_age: int | None
    min_dob: date | None
    max_dob: date | None

    # First & last events with matches (joined to dim_events)
    first_senior_event: EventBoundary | None
    last_senior_event: EventBoundary | None
    first_catalog_event: EventBoundary | None
    last_catalog_event: EventBoundary | None


def data_files_exist() -> bool:
    """Return True when all required Parquet snapshots are present."""
    required = (
        "dim_events.parquet",
        "dim_players.parquet",
        "fct_senior_singles_matches.parquet",
        "catalog_matches_by_event_gender.parquet",
    )
    return all((DATA_DIR / name).exists() for name in required)


@st.cache_data(show_spinner=False)
def load_snapshot_timestamp() -> str | None:
    """Return the UTC timestamp of the last successful data collection."""
    meta_path = DATA_DIR / "snapshot_metadata.json"
    if meta_path.exists():
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            ts = payload.get("last_collected_at_utc") or payload.get("exported_at_utc")
            if isinstance(ts, str) and ts:
                return ts
        except (OSError, json.JSONDecodeError):
            pass

    events_path = DATA_DIR / "dim_events.parquet"
    if events_path.exists():
        mtime = datetime.fromtimestamp(events_path.stat().st_mtime, tz=UTC)
        return mtime.strftime("%Y-%m-%d %H:%M UTC")
    return None


@st.cache_data(show_spinner=False)
def load_events() -> pl.DataFrame:
    """Load the events dimension from Parquet."""
    return pl.read_parquet(DATA_DIR / "dim_events.parquet")


@st.cache_data(show_spinner=False)
def load_players() -> pl.DataFrame:
    """Load the players dimension from Parquet."""
    return pl.read_parquet(DATA_DIR / "dim_players.parquet")


@st.cache_data(show_spinner=False)
def load_senior_matches() -> pl.DataFrame:
    """Load senior singles matches from Parquet."""
    return pl.read_parquet(DATA_DIR / "fct_senior_singles_matches.parquet")


@st.cache_data(show_spinner=False)
def load_catalog_rollup() -> pl.DataFrame:
    """Load pre-aggregated catalog match counts per event and gender."""
    return pl.read_parquet(DATA_DIR / "catalog_matches_by_event_gender.parquet")


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


def _compute_age_years(dob: date, ref_date: date) -> int:
    """Return completed years of age on ``ref_date``."""
    years = ref_date.year - dob.year
    if (ref_date.month, ref_date.day) < (dob.month, dob.day):
        years -= 1
    return years


def compute_summary_stats(
    events: pl.DataFrame,
    players: pl.DataFrame,
    senior_matches: pl.DataFrame,
    catalog_rollup: pl.DataFrame,
    *,
    today: date | None = None,
) -> SummaryStats:
    """Compute all warehouse summary metrics from the loaded DataFrames."""
    ref_date = today or date.today()

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

    # Guard against placeholder/future DOBs when computing age range
    valid_dobs = senior_players.filter(
        pl.col("dob").is_not_null()
        & (pl.col("dob") >= date(1900, 1, 1))
        & (pl.col("dob") <= ref_date)
    )
    players_with_dob = valid_dobs.height
    min_dob: date | None = valid_dobs["dob"].min() if players_with_dob > 0 else None  # type: ignore[assignment]
    max_dob: date | None = valid_dobs["dob"].max() if players_with_dob > 0 else None  # type: ignore[assignment]

    min_age = _compute_age_years(max_dob, ref_date) if max_dob is not None else None
    max_age = _compute_age_years(min_dob, ref_date) if min_dob is not None else None

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
        players_with_dob=players_with_dob,
        min_age=min_age,
        max_age=max_age,
        min_dob=min_dob,
        max_dob=max_dob,
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

"""Glicko-2 Ratings Explorer Page.

Displays a fast, filterable, sortable match-level Glicko-2 leaderboard for
Men's and Women's Senior Singles with flags, 90-day rating trends, optional
time-travel, and column-group toggles.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

import polars as pl
import streamlit as st
from lib.data import (
    data_files_exist,
    load_events,
    load_glicko_match_ratings,
    load_players,
    load_snapshot_timestamp,
)
from lib.glicko import build_as_of_event_options, build_glicko_leaderboard
from lib.theme import ensure_standalone_shell

ensure_standalone_shell(load_snapshot_timestamp())

POOL_LABELS: dict[str, Literal["men", "women"]] = {
    "Men's Singles": "men",
    "Women's Singles": "women",
}

PAGE_CSS = """
<style>
div[data-testid="stSegmentedControl"] button {
    min-height: 48px !important;
    font-size: 1.05rem !important;
    font-weight: 700 !important;
}
</style>
"""


@st.cache_data(show_spinner=False)
def _cached_leaderboard(
    ratings_height: int,
    players_height: int,
    gender: Literal["men", "women"],
    as_of_date: date | None,
    min_matches: int,
) -> pl.DataFrame:
    """Compute and cache the Glicko-2 leaderboard for the active pool and filters."""
    _ = (ratings_height, players_height)
    return build_glicko_leaderboard(
        load_glicko_match_ratings(),
        load_players(),
        gender=gender,
        as_of_date=as_of_date,
        max_rd=None,
        min_matches=min_matches,
        trend_days=90,
    )


@st.cache_data(show_spinner=False)
def _cached_event_options(
    ratings_height: int,
    events_height: int,
    gender: Literal["men", "women"],
) -> list[tuple[str, date | None]]:
    """Build and cache chronological as-of tournament dropdown options."""
    _ = (ratings_height, events_height)
    return build_as_of_event_options(
        load_glicko_match_ratings(),
        load_events(),
        gender=gender,
    )


def _table_column_config(
    *,
    show_rating_stats: bool,
    show_record: bool,
    show_profile: bool,
) -> tuple[list[str], dict[str, Any]]:
    """Return ordered column list and Streamlit column formatting."""
    columns: list[str] = ["rank", "flag_url", "player_name", "rating"]
    config: dict[str, Any] = {
        "rank": st.column_config.NumberColumn(
            "#",
            format="%d",
            width=54,
            pinned=True,
            help="Rank within players meeting the current minimum-matches threshold",
        ),
        "flag_url": st.column_config.ImageColumn(
            "Flag",
            width="small",
            help="Represented association flag",
        ),
        "player_name": st.column_config.TextColumn(
            "Player",
            width="medium",
            pinned=True,
        ),
        "rating": st.column_config.NumberColumn(
            "Rating",
            format="%.1f",
            width="small",
            help="Current match-level Glicko-2 rating",
        ),
    }

    if show_rating_stats:
        columns.extend(["trend_delta", "peak_display", "avg_rating", "last_match_date"])
        config.update(
            {
                "trend_delta": st.column_config.NumberColumn(
                    "90d Δ",
                    format="%+.1f",
                    width="small",
                    help="Rating change over the 90 days leading up to the anchor date",
                ),
                "peak_display": st.column_config.TextColumn(
                    "Peak (DD/MM/YY)",
                    width="medium",
                    help="Career peak Glicko-2 rating and date achieved (DD/MM/YY)",
                ),
                "avg_rating": st.column_config.NumberColumn(
                    "Avg",
                    format="%.1f",
                    width="small",
                    help="Career average post-match rating",
                ),
                "last_match_date": st.column_config.DateColumn(
                    "Last Match",
                    format="DD/MM/YY",
                    width="small",
                ),
            }
        )

    if show_record:
        columns.extend(["matches", "wins", "losses", "win_pct"])
        config.update(
            {
                "matches": st.column_config.NumberColumn(
                    "Matches",
                    format="%d",
                    width="small",
                ),
                "wins": st.column_config.NumberColumn("W", format="%d", width="small"),
                "losses": st.column_config.NumberColumn(
                    "L", format="%d", width="small"
                ),
                "win_pct": st.column_config.NumberColumn(
                    "Win %",
                    format="%.1f%%",
                    width="small",
                ),
            }
        )

    if show_profile:
        columns.extend(
            ["continent", "country_name", "org", "age", "hand", "grip", "style"]
        )
        config.update(
            {
                "continent": st.column_config.TextColumn(
                    "Continent",
                    width="small",
                    help="Continent of represented association",
                ),
                "country_name": st.column_config.TextColumn(
                    "Country",
                    width="medium",
                    help="Represented country or association",
                ),
                "org": st.column_config.TextColumn(
                    "Org",
                    width="small",
                    help="Represented association code",
                ),
                "age": st.column_config.NumberColumn(
                    "Age",
                    format="%d",
                    width="small",
                    help="Player age at snapshot export time (raw DOB stays private)",
                ),
                "hand": st.column_config.TextColumn("Hand", width="small"),
                "grip": st.column_config.TextColumn("Grip", width="small"),
                "style": st.column_config.TextColumn("Style", width="small"),
            }
        )

    return columns, config


def _render_page() -> None:
    st.markdown(PAGE_CSS, unsafe_allow_html=True)
    st.title("🏆 Glicko-2 Ratings")
    st.caption(
        "Match-level Glicko-2 skill ratings across senior singles matches "
        "(zero inactivity time decay). All dates are shown in `DD/MM/YY` "
        "(day/month/year) format."
    )

    if not data_files_exist():
        st.warning(
            "Parquet snapshots are missing in `app/data/`. "
            "Run `make app-refresh` to generate them."
        )
        return

    ratings = load_glicko_match_ratings()
    players = load_players()
    events = load_events()

    # Big top-level Men vs Women switcher.
    pool_choice = st.segmented_control(
        "Category",
        options=list(POOL_LABELS.keys()),
        default="Men's Singles",
        selection_mode="single",
        label_visibility="collapsed",
        width="stretch",
        key="glicko_pool_choice",
    )
    active_label = pool_choice if pool_choice in POOL_LABELS else "Men's Singles"
    gender = POOL_LABELS[active_label]

    pool_ratings = ratings.filter(pl.col("gender_category") == gender)
    event_options = _cached_event_options(ratings.height, events.height, gender)

    # Primary controls bar: Min matches, Search, and As-of tournament dropdown.
    ctrl1, ctrl2, ctrl3 = st.columns([1.15, 1.35, 1.3], gap="medium")
    with ctrl1:
        min_matches = st.slider(
            "Minimum rated matches",
            min_value=1,
            max_value=50,
            value=5,
            step=1,
            key="glicko_min_matches",
        )
    with ctrl2:
        search_query = st.text_input(
            "Search player",
            placeholder="e.g. Wang Chuqin, Lebrun, Sun Yingsha...",
            key="glicko_search_player",
        )
    with ctrl3:
        selected_idx = st.selectbox(
            "As-of tournament",
            options=list(range(len(event_options))),
            format_func=lambda idx: event_options[idx][0],
            index=0,
            key=f"glicko_as_of_event_{gender}",
            help="Pick any tournament to view historical ratings as of that event",
        )
        as_of_date: date | None = event_options[selected_idx][1]

    base_lb = _cached_leaderboard(
        ratings.height,
        players.height,
        gender,
        as_of_date,
        min_matches,
    )

    # Optional attribute filters inside a collapsed expander.
    with st.expander("Filter by Continent / Org / Style / Hand / Grip", expanded=False):
        f0, f1, f2, f3, f4 = st.columns(5, gap="small")
        continent_opts = sorted(
            c for c in base_lb["continent"].drop_nulls().unique().to_list() if c
        )
        org_opts = sorted(
            o for o in base_lb["org"].drop_nulls().unique().to_list() if o
        )
        style_opts = sorted(base_lb["style"].drop_nulls().unique().to_list())
        hand_opts = sorted(base_lb["hand"].drop_nulls().unique().to_list())
        grip_opts = sorted(base_lb["grip"].drop_nulls().unique().to_list())

        with f0:
            sel_continents = st.multiselect(
                "Continent", continent_opts, key=f"glicko_continent_{gender}"
            )
        with f1:
            sel_orgs = st.multiselect("Org", org_opts, key=f"glicko_org_{gender}")
        with f2:
            sel_styles = st.multiselect(
                "Style", style_opts, key=f"glicko_style_{gender}"
            )
        with f3:
            sel_hands = st.multiselect("Hand", hand_opts, key=f"glicko_hand_{gender}")
        with f4:
            sel_grips = st.multiselect("Grip", grip_opts, key=f"glicko_grip_{gender}")

    filtered_lb = base_lb
    if search_query.strip():
        needle = search_query.strip().lower()
        filtered_lb = filtered_lb.filter(
            pl.col("player_name").str.to_lowercase().str.contains(needle, literal=True)
        )
    if sel_continents:
        filtered_lb = filtered_lb.filter(pl.col("continent").is_in(sel_continents))
    if sel_orgs:
        filtered_lb = filtered_lb.filter(pl.col("org").is_in(sel_orgs))
    if sel_styles:
        filtered_lb = filtered_lb.filter(pl.col("style").is_in(sel_styles))
    if sel_hands:
        filtered_lb = filtered_lb.filter(pl.col("hand").is_in(sel_hands))
    if sel_grips:
        filtered_lb = filtered_lb.filter(pl.col("grip").is_in(sel_grips))

    # Count rated matches in the active slice for the KPI card.
    scoped_matches = pool_ratings
    if as_of_date is not None:
        scoped_matches = scoped_matches.filter(
            pl.col("rating_date").is_not_null() & (pl.col("rating_date") <= as_of_date)
        )

    # Headline KPIs.
    k1, k2, k3, k4 = st.columns(4, gap="medium")
    with k1:
        if not filtered_lb.is_empty():
            top_row = filtered_lb.row(0, named=True)
            top_org = f" ({top_row['org']})" if top_row["org"] else ""
            st.metric(
                "#1 Ranked (Shown)",
                f"{top_row['player_name']}",
                f"{top_row['rating']:.1f}{top_org}",
            )
        else:
            st.metric("#1 Ranked (Shown)", "—")
    with k2:
        st.metric(
            "Players Shown",
            f"{filtered_lb.height:,}",
            f"of {base_lb.height:,} with {min_matches}+ matches",
        )
    with k3:
        median_raw = (
            filtered_lb["rating"].median() if not filtered_lb.is_empty() else None
        )
        median_val = (
            f"{float(median_raw):.1f}" if isinstance(median_raw, (int, float)) else "—"
        )
        st.metric("Median Rating", median_val)
    with k4:
        st.metric("Rated Matches in Pool", f"{scoped_matches.height:,}")

    st.markdown("<div style='height: 0.5rem;'></div>", unsafe_allow_html=True)

    # Column-group toggles right above the table.
    t1, t2, t3 = st.columns(3, gap="medium")
    with t1:
        show_rating_stats = st.toggle(
            "Rating stats (90d Δ, Peak, Avg)",
            value=True,
            key="glicko_show_rating_stats",
        )
    with t2:
        show_record = st.toggle(
            "Match record (Matches, W, L, Win %)",
            value=True,
            key="glicko_show_record",
        )
    with t3:
        show_profile = st.toggle(
            "Full player info (Continent, Org, Age, Style, Hand, Grip)",
            value=False,
            key="glicko_show_profile",
        )

    if filtered_lb.is_empty():
        st.info("No players match the current filters.")
    else:
        cols, col_config = _table_column_config(
            show_rating_stats=show_rating_stats,
            show_record=show_record,
            show_profile=show_profile,
        )
        st.dataframe(
            filtered_lb.select(cols).to_pandas(),
            hide_index=True,
            width="stretch",
            height=620,
            column_config=col_config,
        )

    st.markdown("<div style='height: 1.5rem;'></div>", unsafe_allow_html=True)
    st.divider()

    with st.expander("About Glicko-2, zero decay & implementation", expanded=False):
        st.markdown(
            "**What is Glicko-2?**\n\n"
            "Developed by Prof. Mark Glickman, Glicko-2 extends Elo by tracking each "
            "player's **rating**, **rating deviation (RD / uncertainty)**, and "
            "**volatility** (consistency). All players start at `1500.0` (RD `350.0`, "
            "volatility `0.06`, `τ = 0.5`) and update after every rating-eligible "
            "senior singles match (`W = 1.0`, `Draw = 0.5`, `L = 0.0`), with Men's and "
            "Women's Singles rated in separate pools.\n\n"
            "**Why zero time decay?**\n\n"
            "Inactivity RD decay is intentionally disabled (`rd_decay_days = None`). "
            "Players regularly compete in domestic and regional leagues not yet "
            "ingested here, so gaps between WTT events must not inflate their "
            "uncertainty or penalize their rating.\n\n"
            "**Implementation & packages**\n\n"
            "Built with a custom pure-Python implementation of Glickman's Glicko-2 "
            "equations and Illinois volatility solver "
            "(`src/tt_stats/analytics/glicko2.py`, using `Polars` and `NumPy` "
            "downstream) rather than an opaque external rating package.\n\n"
            "**90d Δ & privacy**\n\n"
            "`90d Δ` is the rating change over the 90 days ending at the selected "
            "anchor date. Player names are shown verbatim from WTT/ITTF profiles, "
            "and `Age` at export time is shown instead of `dob`."
        )


_render_page()

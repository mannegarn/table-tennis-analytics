"""Biggest Upsets & Glicko-2 Prediction Accuracy Page.

Ranks the biggest senior singles upsets where the pre-match underdog won,
displays tour-wide Glicko-2 prediction accuracy at the top, and plots Expected
vs Actual Favorite Win % calibration at the bottom.
"""

from __future__ import annotations

from typing import Any, Literal

import polars as pl
import streamlit as st
from lib.charts import render_chart
from lib.data import (
    data_files_exist,
    load_events,
    load_glicko_match_ratings,
    load_players,
    load_senior_matches,
    load_snapshot_timestamp,
)
from lib.theme import ensure_standalone_shell
from lib.upsets import (
    AccuracyMetrics,
    RankBy,
    attach_prior_match_counts,
    build_calibration_buckets,
    build_calibration_chart,
    build_upsets_table,
    compute_prediction_accuracy,
)

ensure_standalone_shell(load_snapshot_timestamp())

POOL_LABELS: dict[str, Literal["men", "women"]] = {
    "Men's Singles": "men",
    "Women's Singles": "women",
}

RANK_BY_LABELS: dict[str, RankBy] = {
    "Lowest % Chance of Upset": "loser_prob",
    "Largest Rating Difference": "rating_diff",
}


@st.cache_data(show_spinner=False)
def _cached_ratings_with_prior(ratings_height: int) -> pl.DataFrame:
    _ = ratings_height
    return attach_prior_match_counts(load_glicko_match_ratings())


@st.cache_data(show_spinner=False)
def _cached_accuracy_summary(
    ratings_height: int,
    min_prior_matches: int,
) -> dict[str, AccuracyMetrics]:
    _ = ratings_height
    return compute_prediction_accuracy(
        _cached_ratings_with_prior(ratings_height),
        min_prior_matches=min_prior_matches,
    )


@st.cache_data(show_spinner=False)
def _cached_upsets_table(
    ratings_height: int,
    players_height: int,
    events_height: int,
    matches_height: int,
    gender: Literal["men", "women"],
    min_prior_matches: int,
    rank_by: RankBy,
    year: int | None,
) -> pl.DataFrame:
    _ = (ratings_height, players_height, events_height, matches_height)
    return build_upsets_table(
        _cached_ratings_with_prior(ratings_height),
        load_players(),
        load_events(),
        load_senior_matches(),
        gender=gender,
        min_prior_matches=min_prior_matches,
        rank_by=rank_by,
        year=year,
    )


def _upsets_column_config(
    *,
    show_match_info: bool,
    show_rating_details: bool,
) -> tuple[list[str], dict[str, Any]]:
    columns: list[str] = [
        "rank",
        "winner_flag_url",
        "winner_name",
        "score",
        "loser_flag_url",
        "loser_name",
        "winner_win_prob_pct",
    ]
    if show_rating_details:
        columns.extend(
            [
                "winner_pre_rating",
                "loser_pre_rating",
                "rating_diff",
                "winner_rating_delta",
            ]
        )
    if show_match_info:
        columns.extend(["event_name", "round_code", "rating_date"])

    config: dict[str, Any] = {
        "rank": st.column_config.NumberColumn(
            "#",
            format="%d",
            width=52,
            pinned=True,
        ),
        "rating_date": st.column_config.DateColumn(
            "Date",
            format="DD/MM/YY",
            width="small",
        ),
        "event_name": st.column_config.TextColumn(
            "Tournament",
            width="medium",
        ),
        "round_code": st.column_config.TextColumn(
            "Round",
            width="small",
        ),
        "winner_flag_url": st.column_config.ImageColumn(
            "W Flag",
            width="small",
            help="Winner's represented association flag",
        ),
        "winner_name": st.column_config.TextColumn(
            "Winner",
            width="medium",
            pinned=True,
        ),
        "score": st.column_config.TextColumn(
            "Score",
            width="small",
            help="Games won (Winner - Loser)",
        ),
        "loser_flag_url": st.column_config.ImageColumn(
            "L Flag",
            width="small",
            help="Loser's represented association flag",
        ),
        "loser_name": st.column_config.TextColumn(
            "Loser",
            width="medium",
        ),
        "winner_win_prob_pct": st.column_config.NumberColumn(
            "% Chance of Upset",
            format="%.1f%%",
            width="small",
            help="Pre-match chance Glicko-2 gave the underdog (winner) to win",
        ),
        "winner_pre_rating": st.column_config.NumberColumn(
            "Winner Rtg",
            format="%.1f",
            width="small",
            help="Winner's pre-match Glicko-2 rating",
        ),
        "loser_pre_rating": st.column_config.NumberColumn(
            "Loser Rtg",
            format="%.1f",
            width="small",
            help="Loser's pre-match Glicko-2 rating",
        ),
        "rating_diff": st.column_config.NumberColumn(
            "Rating Diff",
            format="+%.1f",
            width="small",
            help="Pre-match rating gap (Loser minus Winner)",
        ),
        "winner_rating_delta": st.column_config.NumberColumn(
            "Winner Δ",
            format="%+.1f",
            width="small",
            help="Glicko-2 points gained by the winner",
        ),
    }
    return columns, config


def _format_acc_pct(metrics: AccuracyMetrics) -> str:
    if metrics.accuracy_pct is None:
        return "—"
    return f"{metrics.accuracy_pct:.1f}%"


def _render_page() -> None:
    st.title("Biggest Upsets")
    st.caption(
        "Biggest underdog wins in senior singles, ranked by pre-match Glicko-2 "
        "win chance or rating gap, plus overall model accuracy. Dates use `DD/MM/YY`."
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
    matches = load_senior_matches()

    # Big top-level Men vs Women switcher.
    pool_choice = st.segmented_control(
        "Category",
        options=list(POOL_LABELS.keys()),
        default="Men's Singles",
        selection_mode="single",
        label_visibility="collapsed",
        width="stretch",
        key="upsets_pool_choice",
    )
    active_label = pool_choice if pool_choice in POOL_LABELS else "Men's Singles"
    gender = POOL_LABELS[active_label]

    # Controls row.
    c1, c2, c3, c4 = st.columns([1.25, 1.05, 0.75, 1.25], gap="medium")
    with c1:
        min_prior_matches = st.slider(
            "Min prior matches (both players)",
            min_value=0,
            max_value=30,
            value=10,
            step=1,
            key="upsets_min_prior_matches",
            help="Excludes early debut matches where players start near 1500",
        )
    with c2:
        rank_label = st.selectbox(
            "Rank upsets by",
            options=list(RANK_BY_LABELS.keys()),
            index=0,
            key="upsets_rank_by",
        )
        rank_by = RANK_BY_LABELS[rank_label]
    with c3:
        years = sorted(
            {
                int(y)
                for y in ratings.filter(
                    (pl.col("gender_category") == gender)
                    & pl.col("rating_date").is_not_null()
                )["rating_date"]
                .dt.year()
                .unique()
                .to_list()
                if y is not None
            },
            reverse=True,
        )
        year_options: list[str | int] = ["All years", *years]
        selected_year = st.selectbox(
            "Year",
            options=year_options,
            index=0,
            key=f"upsets_year_{gender}",
        )
        year_filter = selected_year if isinstance(selected_year, int) else None
    with c4:
        search_query = st.text_input(
            "Search player or tournament",
            placeholder="e.g. Lebrun, Harimoto, Smash...",
            key="upsets_search",
        )

    all_acc = _cached_accuracy_summary(ratings.height, 0)
    est_acc = _cached_accuracy_summary(ratings.height, min_prior_matches)
    active_est = est_acc[gender]

    # Top prediction accuracy & upset rate KPIs.
    k1, k2, k3, k4 = st.columns(4, gap="medium")
    with k1:
        m_all = all_acc["men"]
        st.metric(
            "Men's Glicko-2 Accuracy (All)",
            _format_acc_pct(m_all),
            f"{m_all.correct_predictions:,} / {m_all.decisive_matches:,} matches",
        )
    with k2:
        w_all = all_acc["women"]
        st.metric(
            "Women's Glicko-2 Accuracy (All)",
            _format_acc_pct(w_all),
            f"{w_all.correct_predictions:,} / {w_all.decisive_matches:,} matches",
        )
    with k3:
        st.metric(
            f"{active_label} Accuracy ({min_prior_matches}+ prior)",
            _format_acc_pct(active_est),
            f"{active_est.correct_predictions:,} / {active_est.decisive_matches:,}",
        )
    with k4:
        upset_rate_str = (
            f"{active_est.upset_rate_pct:.1f}% upset rate"
            if active_est.upset_rate_pct is not None
            else "—"
        )
        st.metric(
            f"{active_label} Upsets ({min_prior_matches}+ prior)",
            f"{active_est.upsets:,}",
            upset_rate_str,
        )

    upsets_df = _cached_upsets_table(
        ratings.height,
        players.height,
        events.height,
        matches.height,
        gender,
        min_prior_matches,
        rank_by,
        year_filter,
    )

    if search_query.strip():
        needle = search_query.strip().lower()
        upsets_df = upsets_df.filter(
            pl.col("winner_name").str.to_lowercase().str.contains(needle, literal=True)
            | pl.col("loser_name").str.to_lowercase().str.contains(needle, literal=True)
            | pl.col("event_name").str.to_lowercase().str.contains(needle, literal=True)
        )

    st.markdown("<div style='height: 0.5rem;'></div>", unsafe_allow_html=True)
    t1, t2 = st.columns(2, gap="medium")
    with t1:
        show_match_info = st.toggle(
            "Extra match info (Tournament, Round, Date)",
            value=True,
            key="upsets_show_match_info",
        )
    with t2:
        show_rating_details = st.toggle(
            "Rating details (Winner Rtg, Loser Rtg, Rating Diff, Winner Δ)",
            value=False,
            key="upsets_show_rating_details",
        )

    if upsets_df.is_empty():
        st.info("No upsets match the current filters.")
    else:
        cols, col_config = _upsets_column_config(
            show_match_info=show_match_info,
            show_rating_details=show_rating_details,
        )
        st.dataframe(
            upsets_df.select(cols).to_pandas(),
            hide_index=True,
            width="stretch",
            height=600,
            column_config=col_config,
        )

    st.markdown("<div style='height: 1.5rem;'></div>", unsafe_allow_html=True)
    st.divider()

    st.subheader("How Accurate Are Glicko-2 Win Probabilities? (1% Buckets)")
    st.markdown(
        f"""
        <div class="tt-insight-card">
            <p style="margin-top: 0; margin-bottom: 0.55rem;">
                Every completed <strong>{active_label}</strong> match with a clear
                pre-match favorite (both players with
                <strong>{min_prior_matches}+</strong> prior rated matches) is grouped
                into <strong>1% buckets</strong> by the win chance Glicko-2 gave the
                favorite (<code>50-51%</code>, <code>51-52%</code>, &hellip;,
                <code>99-100%</code>):
            </p>
            <ul>
                <li>
                    <strong>On the dashed green line:</strong> Glicko-2 matched reality
                    (e.g., 75% favorites won ~75% of the time).
                </li>
                <li>
                    <strong>Above the line:</strong> Favorites won <em>more</em> often
                    than predicted (fewer upsets than expected).
                </li>
                <li>
                    <strong>Below the line:</strong> Favorites won <em>less</em> often
                    than predicted (more upsets than expected).
                </li>
                <li>
                    <strong>Dot size:</strong> Scales with match volume in that 1%
                    bucket (hover any dot for exact match counts and difference).
                </li>
            </ul>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with_prior = _cached_ratings_with_prior(ratings.height)
    buckets = build_calibration_buckets(
        with_prior,
        gender=gender,
        min_prior_matches=min_prior_matches,
    )
    gender_short = "Men" if gender == "men" else "Women"
    render_chart(
        build_calibration_chart(buckets, gender_label=gender_short),
        key=f"upsets_calibration_{gender}_{min_prior_matches}",
    )

    st.divider()
    with st.expander("Methodology & Definitions", expanded=False):
        st.markdown(
            "- **What counts as an upset:** Any completed match where the "
            "player with `< 50%` pre-match Glicko-2 win probability won "
            "(draws and exact `50/50` toss-ups are excluded).\n"
            "- **Why filter by prior matches (`10+` default):** New players "
            "start in the rating pool at `1500.0` (`RD = 350.0`), so early "
            "matches often reflect initial calibration rather than true upsets.\n"
            "- **Match format note:** Pre-match win probabilities come "
            "directly from match-level Glicko-2 ratings and do not adjust "
            "separately for Best-of-5 vs. Best-of-7 length."
        )


_render_page()

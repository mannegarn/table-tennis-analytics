"""Unlikely Winners: Tournament-Level Glicko-2 Upset Champions Page.

Works backwards from each non-team singles tournament Final winner and multiplies
their pre-match Glicko-2 expected win probabilities across qualifying and knockout
rounds (excluding group, placement, and consolation matches).
"""

from __future__ import annotations

import html
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
from lib.glicko import resolve_placeholder_data_uri
from lib.theme import ensure_standalone_shell
from lib.unlikely_winners import (
    TOURNAMENT_TYPE_OPTIONS,
    build_unlikely_winners_chart,
    build_unlikely_winners_table,
    format_win_prob_pct,
    top_unlikely_winners_by_gender,
)
from lib.upsets import attach_prior_match_counts

ensure_standalone_shell(load_snapshot_timestamp())


def _active_theme() -> Literal["dark", "light"]:
    """Return active Streamlit theme ('dark' or 'light'), defaulting to 'dark'."""
    try:
        theme_type = st.context.theme.type
    except Exception:
        return "dark"
    return "light" if theme_type == "light" else "dark"


@st.cache_data(show_spinner=False)
def _cached_ratings_with_prior(ratings_height: int) -> pl.DataFrame:
    _ = ratings_height
    return attach_prior_match_counts(load_glicko_match_ratings())


@st.cache_data(show_spinner=False)
def _cached_unlikely_winners(
    matches_height: int,
    ratings_height: int,
    players_height: int,
    events_height: int,
    min_prior_matches: int,
    tournament_types: tuple[str, ...],
    year: int | None,
    theme: Literal["dark", "light"],
) -> pl.DataFrame:
    _ = (matches_height, ratings_height, players_height, events_height)
    return build_unlikely_winners_table(
        load_senior_matches(),
        _cached_ratings_with_prior(ratings_height),
        load_players(),
        load_events(),
        min_prior_matches=min_prior_matches,
        tournament_types=tournament_types if tournament_types else None,
        year=year,
        theme=theme,
    )


def _render_top10_list(
    df: pl.DataFrame,
    *,
    gender: Literal["men", "women"],
    theme: Literal["dark", "light"],
) -> None:
    """Render Top 10 unlikely tournament winners with small photos, flags, and odds."""
    if df.is_empty():
        st.info("No tournament winners match the current filters.")
        return

    fallback_uri = html.escape(
        resolve_placeholder_data_uri(gender, theme=theme),
        quote=True,
    )
    rows_html: list[str] = []
    for row in df.to_dicts():
        rank = int(row["rank"])
        rank_cls = "tt-uw-rank tt-uw-rank-1" if rank == 1 else "tt-uw-rank"
        winner_name = html.escape(str(row["winner_name"] or "Unknown Player"))
        winner_org = html.escape(str(row["winner_org"] or ""))
        photo_url = html.escape(
            str(row["winner_photo_url"] or fallback_uri),
            quote=True,
        )

        flag_url_raw = row["winner_flag_url"]
        flag_src = html.escape(str(flag_url_raw), quote=True) if flag_url_raw else ""
        flag_html = (
            f'<img class="tt-uw-flag" src="{flag_src}" alt="{winner_org}" />'
            if flag_src
            else ""
        )

        event_name = html.escape(str(row["event_name"] or "Unknown Event"))
        event_year = row["event_year"]
        tournament_label = (
            f"{event_name} · {int(event_year)}"
            if event_year is not None
            else event_name
        )

        loser_name = html.escape(str(row["loser_name"] or "Unknown Player"))
        score_raw = row["final_score"]
        score_suffix = f" ({html.escape(str(score_raw))})" if score_raw else ""
        final_line = f"def. {loser_name}{score_suffix}"

        prob_str = html.escape(
            format_win_prob_pct(float(row["tournament_win_prob_pct"]))
        )

        rows_html.append(
            '<div class="tt-uw-row">'
            f'<div class="{rank_cls}">{rank}</div>'
            f'<img class="tt-uw-avatar" src="{photo_url}" alt="{winner_name}" '
            f"onerror=\"this.onerror=null;this.src='{fallback_uri}';\" />"
            '<div class="tt-uw-body">'
            '<div class="tt-uw-winner-line">'
            f"{flag_html}"
            f'<span class="tt-uw-name" title="{winner_name}">{winner_name}</span>'
            "</div>"
            f'<div class="tt-uw-tournament" title="{tournament_label}">'
            f"{tournament_label}"
            "</div>"
            f'<div class="tt-uw-final" title="{final_line}">{final_line}</div>'
            "</div>"
            '<div class="tt-uw-prob">'
            f'<div class="tt-uw-prob-val">{prob_str}</div>'
            '<div class="tt-uw-prob-lbl">Knockout Run Chance</div>'
            "</div>"
            "</div>"
        )

    st.markdown(
        f'<div class="tt-uw-list">{"".join(rows_html)}</div>',
        unsafe_allow_html=True,
    )


def _table_column_config() -> tuple[list[str], dict[str, Any]]:
    columns = [
        "rank",
        "winner_flag_url",
        "winner_name",
        "final_score",
        "loser_flag_url",
        "loser_name",
        "tournament_win_prob_pct",
        "event_name",
        "final_date",
    ]
    config: dict[str, Any] = {
        "rank": st.column_config.NumberColumn(
            "#",
            format="%d",
            width=52,
            pinned=True,
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
        "final_score": st.column_config.TextColumn(
            "Score",
            width="small",
            help="Final match set score (Winner - Loser)",
        ),
        "loser_flag_url": st.column_config.ImageColumn(
            "L Flag",
            width="small",
            help="Final runner-up's represented association flag",
        ),
        "loser_name": st.column_config.TextColumn(
            "Loser",
            width="medium",
        ),
        "tournament_win_prob_pct": st.column_config.NumberColumn(
            "Knockout Run Chance",
            format="%.2f%%",
            width="small",
            help=(
                "Product of pre-match Glicko-2 win chances along the champion's "
                "realized qualifying + knockout path (excludes group matches)"
            ),
        ),
        "event_name": st.column_config.TextColumn(
            "Tournament",
            width="large",
        ),
        "final_date": st.column_config.DateColumn(
            "Date",
            format="DD/MM/YY",
            width="small",
        ),
    }
    return columns, config


def _render_page() -> None:
    st.title("Unlikely Winners")
    st.caption(
        "Singles champions who overcame the longest pre-match odds to win a "
        "title. Knockout Run Chance multiplies the champion's pre-match "
        "Glicko-2 win probabilities across every qualifying and knockout match "
        "they won."
    )

    if not data_files_exist():
        st.warning(
            "Parquet snapshots are missing in `app/data/`. "
            "Run `make app-refresh` to generate them."
        )
        return

    matches = load_senior_matches()
    ratings = load_glicko_match_ratings()
    players = load_players()
    events = load_events()
    theme = _active_theme()

    # Compact filter bar.
    c1, c2, c3, c4 = st.columns([1.15, 1.35, 0.7, 1.15], gap="medium")
    with c1:
        min_prior_matches = st.slider(
            "Min prior matches (champion entering run)",
            min_value=0,
            max_value=30,
            value=10,
            step=1,
            key="uw_min_prior_matches",
            help="Excludes early debut runs where players start near 1500",
        )
    with c2:
        selected_types = st.multiselect(
            "Tournament type",
            options=list(TOURNAMENT_TYPE_OPTIONS),
            default=[],
            placeholder="All tournament types",
            key="uw_tournament_types",
        )
    with c3:
        years = sorted(
            {
                int(y)
                for y in events["event_year"].drop_nulls().unique().to_list()
                if y is not None
            },
            reverse=True,
        )
        year_options: list[str | int] = ["All years", *years]
        selected_year = st.selectbox(
            "Year",
            options=year_options,
            index=0,
            key="uw_year",
        )
        year_filter = selected_year if isinstance(selected_year, int) else None
    with c4:
        search_query = st.text_input(
            "Search player or tournament",
            placeholder="e.g. Gacina, Calderano, Doha...",
            key="uw_search",
        )

    types_key = tuple(selected_types)
    winners_df = _cached_unlikely_winners(
        matches.height,
        ratings.height,
        players.height,
        events.height,
        min_prior_matches,
        types_key,
        year_filter,
        theme,
    )

    if search_query.strip():
        needle = search_query.strip().lower()
        winners_df = winners_df.filter(
            pl.col("winner_name").str.to_lowercase().str.contains(needle, literal=True)
            | pl.col("loser_name").str.to_lowercase().str.contains(needle, literal=True)
            | pl.col("event_name").str.to_lowercase().str.contains(needle, literal=True)
        )

    st.divider()
    st.markdown("### Top 10 Most Unlikely Winners")
    types_display = (
        ", ".join(selected_types) if selected_types else "All tournament types"
    )
    st.caption(
        "Lowest knockout-run probabilities in Men's and Women's Singles · "
        f"Showing: {types_display}"
    )

    men_top10 = top_unlikely_winners_by_gender(winners_df, gender="men", limit=10)
    women_top10 = top_unlikely_winners_by_gender(winners_df, gender="women", limit=10)

    leaderboard_view = st.segmented_control(
        "Leaderboard View",
        options=["Both", "Men's Singles", "Women's Singles"],
        default="Both",
        selection_mode="single",
        label_visibility="collapsed",
        width="stretch",
        key="uw_leaderboard_view",
    )
    active_view = (
        leaderboard_view
        if leaderboard_view in {"Both", "Men's Singles", "Women's Singles"}
        else "Both"
    )

    if active_view == "Men's Singles":
        st.markdown("#### Men's Singles")
        _render_top10_list(men_top10, gender="men", theme=theme)
    elif active_view == "Women's Singles":
        st.markdown("#### Women's Singles")
        _render_top10_list(women_top10, gender="women", theme=theme)
    else:
        col_m, col_w = st.columns(2, gap="large")
        with col_m:
            st.markdown("#### Men's Singles")
            _render_top10_list(men_top10, gender="men", theme=theme)
        with col_w:
            st.markdown("#### Women's Singles")
            _render_top10_list(women_top10, gender="women", theme=theme)

    st.divider()
    st.markdown("### Tournament vs. Winner Knockout Run Chance")
    p1, p2 = st.columns([1.4, 1.0], gap="medium")
    with p1:
        category_choice = st.segmented_control(
            "Category",
            options=["Both", "Men's Singles", "Women's Singles"],
            default="Both",
            selection_mode="single",
            label_visibility="collapsed",
            key="uw_category_filter",
        )
    with p2:
        log_scale = st.toggle(
            "Log scale Y-axis",
            value=True,
            key="uw_log_scale",
            help=(
                "Log scale spreads out sub-1% Cinderella runs alongside heavy favorites"
            ),
        )

    scoped_df = winners_df
    if category_choice == "Men's Singles":
        scoped_df = winners_df.filter(pl.col("gender_category") == "men")
    elif category_choice == "Women's Singles":
        scoped_df = winners_df.filter(pl.col("gender_category") == "women")

    if not scoped_df.is_empty():
        scoped_df = scoped_df.with_columns(
            pl.int_range(1, pl.len() + 1, dtype=pl.Int64).alias("rank")
        )

    render_chart(
        build_unlikely_winners_chart(scoped_df, log_scale=log_scale),
        key=(
            f"uw_chart_{category_choice}_{log_scale}_"
            f"{min_prior_matches}_{year_filter}_{types_key}"
        ),
    )

    st.divider()
    st.markdown("### All Tournament Winners")
    if scoped_df.is_empty():
        st.info("No tournament winners match the current filters.")
    else:
        cols, col_config = _table_column_config()
        st.dataframe(
            scoped_df.select(cols).to_pandas(),
            hide_index=True,
            width="stretch",
            height=560,
            column_config=col_config,
        )

    st.divider()
    with st.expander("Methodology & Scope Notes", expanded=False):
        st.markdown(
            "- **How Knockout Run Chance is calculated:** Multiplies the "
            "champion's pre-match Glicko-2 win probability across each qualifying "
            "and knockout win through the Final (`round_code = 'F'`). Unrated "
            "wins (such as walkovers or defaults) count as `1.0`.\n"
            "- **What is excluded:** Team events (`is_team_rubber`), group-stage "
            "matches, placement matches, and consolation rounds are excluded so "
            "only direct elimination and qualifying wins count.\n"
            "- **Actual path vs. pre-tournament odds:** Measures the difficulty "
            "of the opponents the champion actually beat, rather than full "
            "pre-tournament bracket odds (which would require simulating every "
            "possible draw before round one).\n"
            "- **Prior-match filter & format:** Champions must have at least the "
            "selected number of prior rated matches (`10+` by default) entering "
            "the run so brand-new `1500`-rated players do not skew the list. "
            "Best-of-5 vs. Best-of-7 differences are not adjusted separately."
        )


_render_page()

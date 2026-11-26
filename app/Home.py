"""Table Tennis Analytics — App Entrypoint, Router & Landing Page (Home).

Uses ``st.navigation(..., position="hidden")`` paired with a custom grouped
left sidebar separating Overview pages (Home, About, Data Overview) from the
Analytics menu (Glicko-2 Ratings, Win Rates, Upsets, Service Win Rate).
"""

from __future__ import annotations

import html
from typing import Any, Literal

import polars as pl
import streamlit as st
from lib.data import (
    data_files_exist,
    load_glicko_match_ratings,
    load_players,
    load_snapshot_timestamp,
)
from lib.glicko import build_glicko_leaderboard, resolve_placeholder_data_uri
from lib.theme import (
    apply_page_theme,
    render_sidebar_nav,
)

# Page configuration must be the first Streamlit call in the router entrypoint.
st.set_page_config(
    page_title="Table Tennis Analytics",
    page_icon="🏓",
    layout="wide",
    initial_sidebar_state="auto",
)


def _active_theme() -> Literal["dark", "light"]:
    """Return active Streamlit theme ('dark' or 'light'), defaulting to 'dark'."""
    try:
        theme_type = st.context.theme.type
    except Exception:
        return "dark"
    return "light" if theme_type == "light" else "dark"


@st.cache_data(show_spinner=False)
def _cached_home_top5(
    ratings_height: int,
    players_height: int,
    gender: Literal["men", "women"],
    theme: Literal["dark", "light"],
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Build Top 5 highest-rated and Top 5 most-matches tables for Home."""
    _ = (ratings_height, players_height)
    ratings = load_glicko_match_ratings()
    players = load_players()

    top_rated = build_glicko_leaderboard(
        ratings,
        players,
        gender=gender,
        min_matches=5,
        trend_days=90,
        theme=theme,
    ).head(5)

    most_matches = (
        build_glicko_leaderboard(
            ratings,
            players,
            gender=gender,
            min_matches=1,
            trend_days=90,
            theme=theme,
        )
        .sort(["matches", "rating", "player_sk"], descending=[True, True, False])
        .head(5)
        .with_columns(pl.int_range(1, pl.len() + 1, dtype=pl.Int64).alias("rank"))
    )
    return top_rated, most_matches


def _render_showcase_list(
    df: pl.DataFrame,
    *,
    mode: Literal["rated", "matches"],
    gender: Literal["men", "women"],
    theme: Literal["dark", "light"],
) -> None:
    """Render Top 5 players with big rank numbers, circular photos, flags, and stats."""
    fallback_uri = html.escape(
        resolve_placeholder_data_uri(gender, theme=theme),
        quote=True,
    )
    rows_html: list[str] = []
    for row in df.to_dicts():
        rank = int(row["rank"])
        rank_cls = "tt-top5-rank tt-top5-rank-1" if rank == 1 else "tt-top5-rank"
        name = html.escape(str(row["player_name"] or "Unknown Player"))
        org = html.escape(str(row["org"] or ""))
        country = html.escape(str(row["country_name"] or org or "Unknown"), quote=True)
        photo_url = html.escape(str(row["photo_url"] or fallback_uri), quote=True)
        flag_url_raw = row["flag_url"]
        flag_src = html.escape(str(flag_url_raw), quote=True) if flag_url_raw else ""
        flag_html = (
            f'<img class="tt-top5-flag" src="{flag_src}" '
            f'alt="{org}" title="{country}" />'
            if flag_src
            else ""
        )
        meta_html = (
            f'<div class="tt-top5-meta">{flag_html}<span>{org or country}</span></div>'
            if (flag_html or org)
            else ""
        )

        rating_val = float(row["rating"])
        win_pct_val = float(row["win_pct"])
        matches_val = int(row["matches"])

        if mode == "rated":
            stats_html = (
                '<div class="tt-top5-stats">'
                '<div class="tt-top5-stat">'
                f'<div class="tt-top5-stat-val">{rating_val:.1f}</div>'
                '<div class="tt-top5-stat-lbl">Rating</div>'
                "</div>"
                '<div class="tt-top5-stat">'
                f'<div class="tt-top5-stat-sub">{win_pct_val:.1f}%</div>'
                '<div class="tt-top5-stat-lbl">Win %</div>'
                "</div>"
                "</div>"
            )
        else:
            stats_html = (
                '<div class="tt-top5-stats">'
                '<div class="tt-top5-stat">'
                f'<div class="tt-top5-stat-val">{matches_val:,}</div>'
                '<div class="tt-top5-stat-lbl">Matches</div>'
                "</div>"
                '<div class="tt-top5-stat">'
                f'<div class="tt-top5-stat-sub">{win_pct_val:.1f}%</div>'
                '<div class="tt-top5-stat-lbl">Win %</div>'
                "</div>"
                '<div class="tt-top5-stat">'
                f'<div class="tt-top5-stat-sub">{rating_val:.1f}</div>'
                '<div class="tt-top5-stat-lbl">Rating</div>'
                "</div>"
                "</div>"
            )

        rows_html.append(
            '<div class="tt-top5-row">'
            f'<div class="{rank_cls}">{rank}</div>'
            f'<img class="tt-top5-avatar" src="{photo_url}" alt="{name}" '
            f"onerror=\"this.onerror=null;this.src='{fallback_uri}';\" />"
            '<div class="tt-top5-player">'
            f'<div class="tt-top5-name" title="{name}">{name}</div>'
            f"{meta_html}"
            "</div>"
            f"{stats_html}"
            "</div>"
        )

    st.markdown(
        f'<div class="tt-top5-list">{"".join(rows_html)}</div>',
        unsafe_allow_html=True,
    )


def _render_home(
    *,
    glicko_ratings_page: Any,
    upsets_page: Any,
    about_page: Any,
) -> None:
    """Render the clean Home landing page with Top 5 snapshots."""
    st.title("🏓 Table Tennis Analytics")
    st.subheader(
        "Glicko-2 skill ratings, upsets, match outcomes, and point-by-point "
        "analytics across professional senior singles table tennis."
    )

    c1, c2, c3 = st.columns(3, gap="small")
    with c1:
        st.page_link(
            glicko_ratings_page,
            label="Glicko-2 Ratings",
            icon="🏆",
            width="stretch",
        )
    with c2:
        st.page_link(
            upsets_page,
            label="Biggest Upsets",
            icon="⚡",
            width="stretch",
        )
    with c3:
        st.page_link(
            about_page,
            label="About & Architecture",
            icon="📖",
            width="stretch",
        )
    st.caption(
        "Open the sidebar menu (top-left on mobile) to explore full ratings, "
        "upsets, win-rate models, and serve/receive breakdowns."
    )

    if not data_files_exist():
        st.warning(
            "Parquet snapshots are missing in `app/data/`. "
            "Run `make app-refresh` to generate them."
        )
        return

    ratings = load_glicko_match_ratings()
    players = load_players()
    theme = _active_theme()
    men_rated, men_matches = _cached_home_top5(
        ratings.height, players.height, "men", theme
    )
    women_rated, women_matches = _cached_home_top5(
        ratings.height, players.height, "women", theme
    )

    st.divider()
    st.markdown("### 🏆 Highest Rated Currently (Top 5)")
    st.caption(
        "Current Glicko-2 leaders across senior singles (minimum 5 rated matches)."
    )
    col_m, col_w = st.columns(2, gap="large")
    with col_m:
        st.markdown("#### Men's Singles")
        _render_showcase_list(men_rated, mode="rated", gender="men", theme=theme)
    with col_w:
        st.markdown("#### Women's Singles")
        _render_showcase_list(women_rated, mode="rated", gender="women", theme=theme)

    st.markdown("<div style='height: 1.25rem;'></div>", unsafe_allow_html=True)
    st.markdown("### 🏓 Most Matches Played (Top 5)")
    st.caption("Most active senior singles players in the rating pool.")
    col_m2, col_w2 = st.columns(2, gap="large")
    with col_m2:
        st.markdown("#### Men's Singles")
        _render_showcase_list(men_matches, mode="matches", gender="men", theme=theme)
    with col_w2:
        st.markdown("#### Women's Singles")
        _render_showcase_list(
            women_matches, mode="matches", gender="women", theme=theme
        )


about_page = st.Page(
    "pages/1_About.py",
    title="About",
    icon="📖",
    url_path="about",
)
overview_page = st.Page(
    "pages/2_Data_Overview.py",
    title="Data Overview",
    icon="📊",
    url_path="data-overview",
)
win_rates_page = st.Page(
    "pages/3_Win_Rates.py",
    title="Win Rates",
    icon="📈",
    url_path="win-rates",
)
service_win_rate_page = st.Page(
    "pages/4_Service_Win_Rate.py",
    title="Service Win Rate",
    icon="🎯",
    url_path="service-win-rate",
)
glicko_ratings_page = st.Page(
    "pages/5_Glicko_Ratings.py",
    title="Glicko-2 Ratings",
    icon="🏆",
    url_path="glicko-ratings",
)
upsets_page = st.Page(
    "pages/6_Upsets.py",
    title="Upsets",
    icon="⚡",
    url_path="upsets",
)


def _home_entry() -> None:
    _render_home(
        glicko_ratings_page=glicko_ratings_page,
        upsets_page=upsets_page,
        about_page=about_page,
    )


home_page = st.Page(
    _home_entry,
    title="Home",
    icon="🏓",
    default=True,
)

overview_pages = [home_page, about_page, overview_page]
analytics_pages = [
    glicko_ratings_page,
    win_rates_page,
    upsets_page,
    service_win_rate_page,
]

# Register routes with st.navigation first so deep links (/win-rates, etc.)
# resolve cleanly before rendering sidebar page links.
active_page = st.navigation(
    overview_pages + analytics_pages,
    position="hidden",
)

apply_page_theme()
st.session_state["_tt_router_active"] = True
render_sidebar_nav(
    overview_pages=overview_pages,
    analytics_pages=analytics_pages,
    snapshot_timestamp=load_snapshot_timestamp(),
)

active_page.run()

"""Table Tennis Analytics — App Entrypoint, Router & Landing Page (Home).

Uses ``st.navigation(..., position="hidden")`` paired with a custom grouped
left sidebar so Overview pages (Home, About, Data Overview) stay cleanly
separated from the collapsible, distinctly styled Analytics menu (Win Rates,
and upcoming Serve & Receive / Glicko-2 Ratings pages).
"""

from __future__ import annotations

from typing import Any

import streamlit as st
from lib.data import (
    HomeInsights,
    compute_home_insights,
    data_files_exist,
    load_catalog_rollup,
    load_events,
    load_players,
    load_senior_games,
    load_senior_matches,
    load_serve_receive_stats,
    load_snapshot_timestamp,
)
from lib.theme import (
    GITHUB_REPO_URL,
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


@st.cache_data(show_spinner=False)
def _load_cached_home_insights(
    matches_height: int,
    games_height: int,
    players_height: int,
    sr_height: int,
) -> HomeInsights:
    """Compute and cache Home page warehouse scale & headline findings."""
    _ = (matches_height, games_height, players_height, sr_height)
    return compute_home_insights(
        events=load_events(),
        players=load_players(),
        senior_matches=load_senior_matches(),
        senior_games=load_senior_games(),
        catalog_rollup=load_catalog_rollup(),
        serve_receive_raw=load_serve_receive_stats(),
    )


def _render_home(
    *,
    overview_page: Any,
    about_page: Any,
    win_rates_page: Any,
    service_win_rate_page: Any,
) -> None:
    """Render the landing page content."""
    st.title("🏓 Table Tennis Analytics")
    st.subheader(
        "Match outcomes, point-by-point breakdowns, and skill ratings "
        "models across professional table tennis matches."
    )

    st.markdown(
        """
        <div class="tt-pill-row">
            <span class="tt-pill">Python 3.13</span>
            <span class="tt-pill">PostgreSQL 16</span>
            <span class="tt-pill">dbt</span>
            <span class="tt-pill">Polars &amp; Parquet</span>
            <span class="tt-pill">Plotly</span>
            <span class="tt-pill">Gemini Vision</span>
            <span class="tt-pill">Streamlit</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    cta1, cta2, _ = st.columns(
        [1.2, 1.25, 2.55],
        vertical_alignment="center",
    )
    with cta1:
        st.link_button(
            "View Code on GitHub",
            GITHUB_REPO_URL,
            icon="💻",
            width="stretch",
        )
    with cta2:
        st.page_link(
            about_page,
            label="Architecture & About",
            icon="📖",
            width="stretch",
        )

    insights: HomeInsights | None = None
    if data_files_exist():
        insights = _load_cached_home_insights(
            load_senior_matches().height,
            load_senior_games().height,
            load_players().height,
            load_serve_receive_stats().height,
        )

    if insights is not None:
        st.divider()
        year_span = (
            f"{insights.senior_year_min}-{insights.senior_year_max}"
            if insights.senior_year_min and insights.senior_year_max
            else "WTT & ITTF"
        )
        m1, m2, m3, m4 = st.columns(4)
        m1.metric(
            "Senior Singles Matches",
            f"{insights.senior_total_matches:,}",
            help=f"Active senior singles span: {year_span}.",
        )
        m2.metric(
            "Point-by-point plays",
            f"{insights.commentary_points:,}",
            help=(
                f"Parsed across {insights.commentary_matches:,} "
                "with full point by point breakdown."
            ),
        )
        m3.metric(
            "Senior Players",
            f"{insights.total_players:,}",
            help=(
                f"Representing {insights.distinct_countries:,} "
                "countries & associations."
            ),
        )
        m4.metric(
            "Events Covered",
            f"{insights.events_with_senior_singles:,}",
            help=f"Tournaments with senior singles records ({year_span}).",
        )

        st.markdown("### What the Data Reveals")
        d1, d2, d3 = st.columns(3)

        with d1:
            edge_str = (
                f"+{insights.server_edge_pp:.2f} pp"
                if insights.server_edge_pp is not None
                else "N/A"
            )
            srv_str = (
                f"{insights.global_serve_win_pct:.2f}%"
                if insights.global_serve_win_pct is not None
                else "N/A"
            )
            rcv_str = (
                f"{insights.global_receive_win_pct:.2f}%"
                if insights.global_receive_win_pct is not None
                else "N/A"
            )
            st.markdown(
                f"""
                <div class="tt-discovery-card">
                    <div class="tt-discovery-stat">{edge_str}</div>
                    <div class="tt-discovery-title">Server Advantage</div>
                    <div class="tt-discovery-body">
                        Across {insights.commentary_points:,} points, servers win
                        <strong>{srv_str}</strong> on serve vs
                        <strong>{rcv_str}</strong> on receive.
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.page_link(
                service_win_rate_page,
                label="Explore Serve & Receive",
                icon="🎯",
                width="stretch",
            )

        with d2:
            pct_str = (
                f"{insights.bo5_won_below_50_pct:.2f}%"
                if insights.bo5_won_below_50_pct is not None
                else "N/A"
            )
            st.markdown(
                f"""
                <div class="tt-discovery-card">
                    <div class="tt-discovery-stat">{pct_str}</div>
                    <div class="tt-discovery-title">Won With &lt;50% of Points</div>
                    <div class="tt-discovery-body">
                        <strong>{insights.bo5_won_below_50:,}</strong> of
                        {insights.bo5_matches:,} Best-of-5 winners lost the total
                        point count yet won the match by clustering points into sets.
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.page_link(
                win_rates_page,
                label="Explore Win Rates",
                icon="📈",
                width="stretch",
            )

        with d3:
            bo5_def = (
                f"{insights.defender_bo5_delta_pp:+.1f} pp"
                if insights.defender_bo5_delta_pp is not None
                else "N/A"
            )
            bo7_def = (
                f"{insights.defender_bo7_delta_pp:+.1f} pp"
                if insights.defender_bo7_delta_pp is not None
                else "N/A"
            )
            set_def = (
                f"{insights.defender_set_delta_pp:+.2f} pp"
                if insights.defender_set_delta_pp is not None
                else "N/A"
            )
            st.markdown(
                f"""
                <div class="tt-discovery-card">
                    <div class="tt-discovery-stat">{bo5_def} / {bo7_def}</div>
                    <div class="tt-discovery-title">The Defender Match Gap</div>
                    <div class="tt-discovery-body">
                        Choppers match binomial theory in 11-pt sets
                        (<code>{set_def}</code>), yet trail in Bo5 and Bo7 as
                        attackers adapt.
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.page_link(
                win_rates_page,
                label="Compare Styles vs Theory",
                icon="📉",
                width="stretch",
            )

    st.divider()

    # -----------------------------------------------------------------------
    # Explore Pages (Navigation Cards)
    # -----------------------------------------------------------------------
    st.markdown("### Explore")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(
            """
            <div class="tt-card">
                <div class="tt-card-label">Analytics</div>
                <div class="tt-card-title">📈 Win Rates</div>
                <div class="tt-card-meta">
                    Point, Set, and Match win rates vs exact binomial models,
                    Defender vs Attacker comparisons, and Best-of-5 vs Best-of-7.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link(win_rates_page, label="Open Win Rates", icon="📈", width="stretch")

    with col2:
        st.markdown(
            """
            <div class="tt-card">
                <div class="tt-card-label">Analytics</div>
                <div class="tt-card-title">🎯 Service Win Rate</div>
                <div class="tt-card-meta">
                    Point-by-point Total, Serve, and Receive win rates across
                    commentary-covered matches, plus global server advantage.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link(
            service_win_rate_page,
            label="Open Service Win Rate",
            icon="🎯",
            width="stretch",
        )

    col3, col4 = st.columns(2)
    with col3:
        st.markdown(
            """
            <div class="tt-card">
                <div class="tt-card-label">Overview</div>
                <div class="tt-card-title">📊 Data Overview</div>
                <div class="tt-card-meta">
                    Warehouse coverage: events, Men vs Women players &amp; matches,
                    Best-of-5 vs Best-of-7 formats, and tournament date ranges.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link(
            overview_page, label="Open Data Overview", icon="📊", width="stretch"
        )

    with col4:
        st.markdown(
            """
            <div class="tt-card">
                <div class="tt-card-label">Overview</div>
                <div class="tt-card-title">📖 About &amp; Architecture</div>
                <div class="tt-card-meta">
                    Project overview and about the author.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link(about_page, label="Open About", icon="📖", width="stretch")

    st.markdown("#### Coming Soon")
    st.markdown(
        """
        <div class="tt-card">
            <div class="tt-card-label">Planned</div>
            <div class="tt-card-title">🏆 Glicko Ratings &amp; Upsets</div>
            <div class="tt-card-meta">
                Historical Glicko-2 ratings by date or tournament, peak ratings,
                rating volatility, and biggest upsets.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.divider()

    # -----------------------------------------------------------------------
    # Inspired By / Credits
    # -----------------------------------------------------------------------
    st.markdown("### Inspired By & Credits")
    st.markdown(
        "- **[JeffSackmann/tennis_MatchChartingProject & tennis_atp]"
        "(https://github.com/JeffSackmann)** — Racquet-sport reference separating "
        "match-level outcomes from granular point-by-point logs, inspiring the "
        "point-vs-match leverage analysis.\n"
        "- **[dbt-labs/jaffle-shop](https://github.com/dbt-labs/jaffle-shop)** — "
        "Canonical dbt reference for layered staged, intermediate, and mart "
        "modeling and schema tests.\n"
        "- **[alan-turing-institute/AIrsenal]"
        "(https://github.com/alan-turing-institute/AIrsenal)** — Python + SQL "
        "sports analytics architecture separating ingestion, relational storage, "
        "strength estimation, and backtesting.\n"
        "- **[sublee/glicko2](https://github.com/sublee/glicko2) & "
        "ryankirkman/pyglicko2** — Lightweight Python reference implementations "
        "of the Glicko-2 update equations.\n"
        '- **Mark Glickman: ["Example of the Glicko-2 System"]'
        "(https://www.glicko.net/glicko/glicko2.pdf)** — Primary paper defining "
        "the Glicko-2 equations, scaling factor (`173.7178`), and iterative "
        "Illinois volatility algorithm."
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


def _home_entry() -> None:
    _render_home(
        overview_page=overview_page,
        about_page=about_page,
        win_rates_page=win_rates_page,
        service_win_rate_page=service_win_rate_page,
    )


home_page = st.Page(
    _home_entry,
    title="Home",
    icon="🏓",
    default=True,
)

overview_pages = [home_page, about_page, overview_page]
analytics_pages = [win_rates_page, service_win_rate_page]

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

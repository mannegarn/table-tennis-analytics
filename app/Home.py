"""Table Tennis Analytics — App Entrypoint, Router & Landing Page (Home).

Uses ``st.navigation(..., position="hidden")`` paired with a custom grouped
left sidebar so Overview pages (Home, About, Data Overview) stay cleanly
separated from the collapsible, distinctly styled Analytics menu (Win Rates,
and upcoming Serve & Receive / Glicko-2 Ratings pages).
"""

from __future__ import annotations

from typing import Any

import streamlit as st
from lib.data import load_snapshot_timestamp
from lib.theme import apply_page_theme, render_sidebar_nav

# Page configuration must be the first Streamlit call in the router entrypoint.
st.set_page_config(
    page_title="Table Tennis Analytics",
    page_icon="🏓",
    layout="wide",
    initial_sidebar_state="auto",
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
        "Match outcomes, player stats, and rating models "
        "from professional table tennis."
    )

    st.divider()

    # -----------------------------------------------------------------------
    # 1. Explore Pages (Navigation Cards)
    # -----------------------------------------------------------------------
    st.markdown("### Explore")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(
            """
            <div class="tt-card">
                <div class="tt-card-label">Analytics · Available Now</div>
                <div class="tt-card-title">📈 Win Rates</div>
                <div class="tt-card-meta">
                    Point, Set, and Match win rates vs exact binomial models,
                    Defender vs Attacker comparisons, and Best-of-5 vs Best-of-7.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link(win_rates_page, label="Open Win Rates", icon="📈")

    with col2:
        st.markdown(
            """
            <div class="tt-card">
                <div class="tt-card-label">Analytics · Available Now</div>
                <div class="tt-card-title">🎯 Service Win Rate</div>
                <div class="tt-card-meta">
                    Point-by-point Total, Serve, and Receive win rates across
                    commentary-covered matches, plus global server advantage.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link(service_win_rate_page, label="Open Service Win Rate", icon="🎯")

    col3, col4 = st.columns(2)
    with col3:
        st.markdown(
            """
            <div class="tt-card">
                <div class="tt-card-label">Overview · Available Now</div>
                <div class="tt-card-title">📊 Data Overview</div>
                <div class="tt-card-meta">
                    Warehouse coverage: events, Men vs Women players &amp; matches,
                    Best-of-5 vs Best-of-7 formats, and tournament date ranges.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link(overview_page, label="Open Data Overview", icon="📊")

    with col4:
        st.markdown(
            """
            <div class="tt-card">
                <div class="tt-card-label">Overview · Available Now</div>
                <div class="tt-card-title">📖 About</div>
                <div class="tt-card-meta">
                    Where the data comes from, how the pipeline is built,
                    the inspiration behind this project, and a bit about me.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link(about_page, label="Open About", icon="📖")

    st.markdown("#### Coming Soon")
    st.markdown(
        """
        <div class="tt-card">
            <div class="tt-card-label">Planned</div>
            <div class="tt-card-title">🏆 Glicko Ratings &amp; Upsets</div>
            <div class="tt-card-meta">
                Historical Glicko ratings by date or tournament, peak ratings,
                and biggest upsets.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.divider()

    # -----------------------------------------------------------------------
    # 2. Inspired By / Credits
    # -----------------------------------------------------------------------
    st.markdown("### Inspired By & Credits")
    st.markdown(
        "- **[JeffSackmann/tennis_MatchChartingProject & tennis_atp]"
        "(https://github.com/JeffSackmann)** - Racquet-sport reference separating "
        "match-level outcomes from granular point-by-point logs, inspiring the "
        "point-vs-match leverage analysis.\n"
        "- **[dbt-labs/jaffle-shop](https://github.com/dbt-labs/jaffle-shop)** - "
        "Canonical dbt reference for layered `staging/`, `intermediate/`, and "
        "`marts/` (`dim_*` and `fct_*`) modeling and schema tests.\n"
        "- **[alan-turing-institute/AIrsenal]"
        "(https://github.com/alan-turing-institute/AIrsenal)** - Python + SQL "
        "sports analytics architecture separating ingestion, relational storage, "
        "strength estimation, and backtesting.\n"
        "- **[sublee/glicko2](https://github.com/sublee/glicko2) & "
        "ryankirkman/pyglicko2** - Lightweight Python reference implementations "
        "of the Glicko-2 update equations.\n"
        '- **Mark Glickman: ["Example of the Glicko-2 System"]'
        "(https://www.glicko.net/glicko/glicko2.pdf)** - Primary paper defining "
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

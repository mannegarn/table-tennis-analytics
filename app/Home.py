"""Table Tennis Analytics — Landing Page (Home).

Provides a quick subheader, direct navigation links to each page,
and an Inspired By / Credits section with editable placeholders.
"""

from __future__ import annotations

import streamlit as st
from lib.data import load_snapshot_timestamp
from lib.theme import apply_page_theme, render_sidebar_footer

# Page configuration must be the first Streamlit call on the page.
st.set_page_config(
    page_title="Table Tennis Analytics",
    page_icon="🏓",
    layout="wide",
    initial_sidebar_state="expanded",
)

apply_page_theme()

# ---------------------------------------------------------------------------
# 1. Header & Subheader
# ---------------------------------------------------------------------------
st.title("🏓 Table Tennis Analytics")
st.subheader(
    "Match outcomes, player stats, and rating models from professional table tennis."
)

st.divider()

# ---------------------------------------------------------------------------
# 2. Explore Pages (Navigation Cards)
# ---------------------------------------------------------------------------
st.markdown("### Explore")

col1, col2 = st.columns(2)
with col1:
    st.markdown(
        """
        <div class="tt-card">
            <div class="tt-card-label">Available Now</div>
            <div class="tt-card-title">📊 Data Overview</div>
            <div class="tt-card-meta">
                Warehouse coverage: events, Men vs Women players &amp; matches,
                Best-of-5 vs Best-of-7 formats, and tournament date ranges.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.page_link("pages/2_Data_Overview.py", label="Open Data Overview", icon="📊")

with col2:
    st.markdown(
        """
        <div class="tt-card">
            <div class="tt-card-label">Available Now</div>
            <div class="tt-card-title">📖 About</div>
            <div class="tt-card-meta">
                Where the data comes from, how the pipeline is built,
                the inspiration behind this project, and a bit about me.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.page_link("pages/1_About.py", label="Open About", icon="📖")

st.markdown("#### Coming Soon")
c_win, c_sr, c_glicko = st.columns(3)
with c_win:
    st.markdown(
        """
        <div class="tt-card">
            <div class="tt-card-label">Planned</div>
            <div class="tt-card-title">📈 Win Rates</div>
            <div class="tt-card-meta">
                Match vs Set vs Point win rates by style, grip, and Best-of format.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with c_sr:
    st.markdown(
        """
        <div class="tt-card">
            <div class="tt-card-label">Planned</div>
            <div class="tt-card-title">🎯 Serve &amp; Receive</div>
            <div class="tt-card-meta">
                Server vs receiver point win rates and top/bottom player deltas.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with c_glicko:
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

# ---------------------------------------------------------------------------
# 3. Inspired By / Credits
# ---------------------------------------------------------------------------
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

render_sidebar_footer(load_snapshot_timestamp())

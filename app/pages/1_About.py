"""About Page — Data sources, architecture, inspiration, and author bio."""

from __future__ import annotations

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
    CONTACT_EMAIL,
    GITHUB_REPO_URL,
    ensure_standalone_shell,
)

ensure_standalone_shell(load_snapshot_timestamp())


@st.cache_data(show_spinner=False)
def _load_cached_about_insights(
    matches_height: int,
    games_height: int,
    players_height: int,
    sr_height: int,
) -> HomeInsights:
    _ = (matches_height, games_height, players_height, sr_height)
    return compute_home_insights(
        events=load_events(),
        players=load_players(),
        senior_matches=load_senior_matches(),
        senior_games=load_senior_games(),
        catalog_rollup=load_catalog_rollup(),
        serve_receive_raw=load_serve_receive_stats(),
    )


st.title("📖 About & Architecture")

st.divider()

# ---------------------------------------------------------------------------
# 1. What This Project Is
# ---------------------------------------------------------------------------
st.subheader("What This Project Is")
st.markdown(
    "This project brings together match outcomes, player profiles, and "
    "point-by-point logs from professional table tennis matches. The goal is "
    "to explore why top players win more matches, investigate whether factors "
    "like table brand or equipment make a measurable difference, and track "
    "historical Glicko-2 player skill ratings and match upsets."
)

if data_files_exist():
    insights = _load_cached_about_insights(
        load_senior_matches().height,
        load_senior_games().height,
        load_players().height,
        load_serve_receive_stats().height,
    )
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
        "Point-by-Point Plays",
        f"{insights.commentary_points:,}",
        help=f"Parsed across {insights.commentary_matches:,} commentary matches.",
    )
    m3.metric(
        "Senior Players",
        f"{insights.total_players:,}",
        help=f"Representing {insights.distinct_countries:,} countries & associations.",
    )
    m4.metric(
        "Events Covered",
        f"{insights.events_with_senior_singles:,}",
        help=f"Tournaments with senior singles records ({year_span}).",
    )
    st.markdown("<div style='height: 0.65rem;'></div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# 2. End-to-End Architecture (For Engineering Reviewers)
# ---------------------------------------------------------------------------
st.subheader("End-to-End Data Platform Architecture")
st.markdown(
    """
    <div class="tt-pipeline-grid">
        <div class="tt-pipeline-step">
            <div class="tt-pipeline-num">1 · Ingest &amp; Vision</div>
            <div class="tt-pipeline-title">Python + Google Gemini API</div>
            <div class="tt-pipeline-desc">
                Async Python collects match and player records, while a Gemini
                Vision pipeline identifies table manufacturers from YouTube
                match footage.
            </div>
        </div>
        <div class="tt-pipeline-step">
            <div class="tt-pipeline-num">2 · Warehouse</div>
            <div class="tt-pipeline-title">PostgreSQL 16 + dbt</div>
            <div class="tt-pipeline-desc">
                Raw payloads land in PostgreSQL before cleaning, identity
                resolution, and mart transformations managed with dbt and
                curated seeds.
            </div>
        </div>
        <div class="tt-pipeline-step">
            <div class="tt-pipeline-num">3 · Analysis</div>
            <div class="tt-pipeline-title">Parquet + Polars</div>
            <div class="tt-pipeline-desc">
                Cleaned marts are exported to Parquet and analyzed with
                Polars &mdash; powering Glicko-2 ratings, upset analysis, and
                binomial win-rate models.
            </div>
        </div>
        <div class="tt-pipeline-step">
            <div class="tt-pipeline-num">4 · Analytics &amp; UI</div>
            <div class="tt-pipeline-title">Streamlit Frontend</div>
            <div class="tt-pipeline-desc">
                Charts are built with Plotly and served in an interactive
                Streamlit web app.
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# 3. Where the Data Comes From
# ---------------------------------------------------------------------------
st.subheader("Where the Data Comes From")
st.markdown(
    "Current sources:\n"
    "- **WTT (World Table Tennis):** "
    "[worldtabletennis.com](https://www.worldtabletennis.com)\n"
    "- **ITTF (International Table Tennis Federation):** "
    "[results.ittf.com](https://results.ittf.com)\n"
    "- **Other leagues:** Coming soon."
)


# ---------------------------------------------------------------------------
# 4. About Me & Contact
# ---------------------------------------------------------------------------
st.subheader("About Me / Inspiration Behind This Project")
st.markdown(
    "I picked up table tennis in London in my mid-20s and enjoy both the "
    "sport and working with data. In major sports such as tennis and football, "
    "there is endless public data and analysis available. Those same "
    "metrics and insights are not widely available for table tennis — "
    "I want to change that."
)

c_gh, c_em = st.columns(2)
with c_gh:
    st.link_button(
        "Source Code on GitHub",
        GITHUB_REPO_URL,
        icon="💻",
        width="stretch",
    )
with c_em:
    st.link_button(
        "Email Marcus",
        f"mailto:{CONTACT_EMAIL}",
        icon="✉️",
        width="stretch",
    )

st.markdown("<div style='height: 0.65rem;'></div>", unsafe_allow_html=True)

with st.expander("Inspired By & Credits", expanded=False):
    st.markdown(
        "- **[JeffSackmann/tennis_MatchChartingProject & tennis_atp]"
        "(https://github.com/JeffSackmann)** — Racquet-sport reference separating "
        "match-level outcomes from granular point-by-point logs.\n"
        "- **[dbt-labs/jaffle-shop](https://github.com/dbt-labs/jaffle-shop)** — "
        "Canonical dbt reference for layered staged, intermediate, and mart "
        "modeling and schema tests.\n"
        "- **[alan-turing-institute/AIrsenal]"
        "(https://github.com/alan-turing-institute/AIrsenal)** — Python + SQL "
        "sports analytics architecture separating ingestion, storage, and "
        "strength estimation.\n"
        '- **Mark Glickman: ["Example of the Glicko-2 System"]'
        "(https://www.glicko.net/glicko/glicko2.pdf)** — Primary paper defining "
        "the Glicko-2 equations, scaling factor (`173.7178`), and Illinois "
        "volatility solver."
    )

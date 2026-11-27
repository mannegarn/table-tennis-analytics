"""About Page: Data sources, architecture, inspiration, and author bio."""

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


st.title("About & Architecture")

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
st.subheader("How It's Built")
st.markdown(
    """
    <div class="tt-pipeline-grid">
        <div class="tt-pipeline-step">
            <div class="tt-pipeline-num">1 · Ingest &amp; Vision</div>
            <div class="tt-pipeline-title">Python + Google Gemini API</div>
            <div class="tt-pipeline-desc">
                Async Python collects match and player records, while a Gemini
                Vision pipeline identifies table brands from YouTube match
                footage.
            </div>
        </div>
        <div class="tt-pipeline-step">
            <div class="tt-pipeline-num">2 · Warehouse</div>
            <div class="tt-pipeline-title">PostgreSQL 16 + dbt</div>
            <div class="tt-pipeline-desc">
                Raw records are stored in PostgreSQL 16, where dbt models and
                reference seeds clean the data, merge duplicate player profiles,
                and build analysis tables.
            </div>
        </div>
        <div class="tt-pipeline-step">
            <div class="tt-pipeline-num">3 · Analysis</div>
            <div class="tt-pipeline-title">Parquet + Polars</div>
            <div class="tt-pipeline-desc">
                Clean tables are exported to Parquet and queried with Polars to
                compute Glicko-2 ratings, upset probabilities, and win-rate
                curves.
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
    "sport and working with data. Sports like tennis and football have endless "
    "public stats and analysis available, while table tennis has very little. "
    "I would like to change that."
)

c_gh, c_em = st.columns(2)
with c_gh:
    st.link_button(
        "Source Code on GitHub",
        GITHUB_REPO_URL,
        icon=":material/code:",
        width="stretch",
    )
with c_em:
    st.markdown(
        f'<a href="mailto:{CONTACT_EMAIL}" '
        'style="display: inline-flex; align-items: center; justify-content: center; '
        "gap: 0.45rem; width: 100%; min-height: 40px; padding: 0.35rem 0.75rem; "
        "border: 1px solid rgba(245, 158, 11, 0.45); border-radius: 8px; "
        "background-color: var(--secondary-background-color); "
        "color: var(--text-color); "
        'text-decoration: none; font-weight: 500; font-size: 0.95rem;">'
        f"Email: {CONTACT_EMAIL}</a>",
        unsafe_allow_html=True,
    )

st.markdown("<div style='height: 0.65rem;'></div>", unsafe_allow_html=True)

with st.expander("Inspired By & Credits", expanded=False):
    st.markdown(
        "- **[JeffSackmann/tennis_MatchChartingProject & tennis_atp]"
        "(https://github.com/JeffSackmann):** Inspiration for separating "
        "match-level results from point-by-point logs.\n"
        "- **[dbt-labs/jaffle-shop](https://github.com/dbt-labs/jaffle-shop):** "
        "Reference structure for layered dbt staging, intermediate, and mart "
        "models with schema tests.\n"
        "- **[alan-turing-institute/AIrsenal]"
        "(https://github.com/alan-turing-institute/AIrsenal):** Sports analytics "
        "setup separating data collection, database storage, and player rating "
        "models.\n"
        '- **Mark Glickman: ["Example of the Glicko-2 System"]'
        "(https://www.glicko.net/glicko/glicko2.pdf):** Original paper defining "
        "the Glicko-2 formulas, scaling factor (`173.7178`), and Illinois "
        "volatility solver."
    )

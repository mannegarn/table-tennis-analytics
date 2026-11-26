"""About Page — Data sources, architecture, inspiration, and author bio."""

from __future__ import annotations

import streamlit as st
from lib.data import load_snapshot_timestamp
from lib.theme import (
    CONTACT_EMAIL,
    GITHUB_REPO_URL,
    ensure_standalone_shell,
)

ensure_standalone_shell(load_snapshot_timestamp())

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
    "like table brand or equipment make a measurable difference, and build "
    "historical player skill ratings (planned)."
)

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
                Async Python code is used to fetch match and player records;
                a Gemini Vision pipeline identifies table manufacturers from
                Youtube match footage.
            </div>
        </div>
        <div class="tt-pipeline-step">
            <div class="tt-pipeline-num">2 · Warehouse</div>
            <div class="tt-pipeline-title">PostgreSQL 16 + dbt</div>
            <div class="tt-pipeline-desc">
                Raw data lands into the database before cleaning and
                transformations managed with DBT. Additional data is seeded
                here as well.
            </div>
        </div>
        <div class="tt-pipeline-step">
            <div class="tt-pipeline-num">3 · Analysis</div>
            <div class="tt-pipeline-title">Parquet + Polars</div>
            <div class="tt-pipeline-desc">
                Cleaned data exported to parquet files - used for analysis with
                Polars. Data is compared to theoretical models such as binomial
                win rate predictions based on % of points scored.
            </div>
        </div>
        <div class="tt-pipeline-step">
            <div class="tt-pipeline-num">4 · Analytics &amp; UI</div>
            <div class="tt-pipeline-title">Streamlit Frontend</div>
            <div class="tt-pipeline-desc">
                Data is plotted using potly and served in a frontend built with
                Streamlit
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

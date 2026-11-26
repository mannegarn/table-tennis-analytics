"""About Page — Data sources, architecture, inspiration, and author bio."""

from __future__ import annotations

import streamlit as st
from lib.data import load_snapshot_timestamp
from lib.theme import (
    CONTACT_EMAIL,
    GITHUB_REPO_URL,
    LINKEDIN_URL,
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
            <div class="tt-pipeline-title">Async Collectors + Gemini</div>
            <div class="tt-pipeline-desc">
                Concurrent <code>httpx</code> collectors fetch WTT &amp; ITTF
                records; a Gemini Vision pipeline identifies missing tournament
                table sponsors from match video.
            </div>
        </div>
        <div class="tt-pipeline-step">
            <div class="tt-pipeline-num">2 · Warehouse</div>
            <div class="tt-pipeline-title">PostgreSQL 16 + dbt</div>
            <div class="tt-pipeline-desc">
                Raw JSONB payloads land transactionally in PostgreSQL and
                transform through staged, intermediate, and mart models with
                manual CSV override seeds.
            </div>
        </div>
        <div class="tt-pipeline-step">
            <div class="tt-pipeline-num">3 · Serving Layer</div>
            <div class="tt-pipeline-title">Parquet + Polars</div>
            <div class="tt-pipeline-desc">
                Curated marts export atomically to compact <code>zstd</code>
                Parquet snapshots (export-time age instead of DOB) for
                millisecond Polars queries.
            </div>
        </div>
        <div class="tt-pipeline-step">
            <div class="tt-pipeline-num">4 · Analytics &amp; UI</div>
            <div class="tt-pipeline-title">Models + Streamlit</div>
            <div class="tt-pipeline-desc">
                Exact 11-pt deuce binomial models, 2-param logistic S-curves,
                and point-by-point server inference rendered interactively with
                Plotly.
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
# 4. Engineering & Quality Practices
# ---------------------------------------------------------------------------
st.subheader("How It's Built & Verified")
st.markdown(
    "- **Collection & Enrichment:** Automated async Python collectors gather "
    "raw match, event, and player records with idempotent landing and run-level "
    "audit logs. Where tournament equipment metadata is missing, a **Gemini** "
    "vision workflow identifies table brands from tournament footage.\n"
    "- **Warehouse & Modelling:** Raw payloads are landed in **PostgreSQL** "
    "and transformed through **dbt** (`staging`, `intermediate`, and `marts` "
    "layers), alongside **manual CSV overrides** (dbt seeds) for edge-case "
    "score, format, sponsor, and player-profile corrections.\n"
    "- **Testing & Quality Tooling:** Backed by unit and PostgreSQL integration "
    "tests (`pytest`, including Streamlit `AppTest` smoke and filter tests), "
    "strict `mypy` type checking, `ruff` linting/formatting, `sqlfluff` SQL "
    "linting, and `dbt` schema/data tests.\n"
    "- **AI Transparency:** The current launch version was developed with AI "
    "assistance using **DeepSeek** and **Google Antigravity**, building upon "
    "an earlier prototype built by hand in Jupyter notebooks."
)

# ---------------------------------------------------------------------------
# 5. About Me & Contact
# ---------------------------------------------------------------------------
st.subheader("About Me / Inspiration Behind This Project")
st.markdown(
    "I picked up table tennis in London in my mid-20s and enjoy both the "
    "sport and working with data. In major sports such as tennis and football, "
    "there is endless public data and analysis available. Those same "
    "metrics and insights are not widely available for table tennis — "
    "I want to change that."
)

c_gh, c_li, c_em = st.columns(3)
with c_gh:
    st.link_button(
        "Source Code on GitHub",
        GITHUB_REPO_URL,
        icon="💻",
        width="stretch",
    )
with c_li:
    st.link_button(
        "Connect on LinkedIn",
        LINKEDIN_URL,
        icon="🔗",
        width="stretch",
    )
with c_em:
    st.link_button(
        "Email Marcus",
        f"mailto:{CONTACT_EMAIL}",
        icon="✉️",
        width="stretch",
    )

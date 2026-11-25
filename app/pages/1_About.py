"""About Page — Data sources, architecture, inspiration, and author bio."""

from __future__ import annotations

import streamlit as st
from lib.data import load_snapshot_timestamp
from lib.theme import apply_page_theme, render_sidebar_footer

st.set_page_config(
    page_title="About | Table Tennis Analytics",
    page_icon="📖",
    layout="wide",
)

apply_page_theme()

st.title("📖 About")

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
# 2. Where the Data Comes From
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
# 3. How It's Built
# ---------------------------------------------------------------------------
st.subheader("How It's Built")
st.markdown(
    "- **Collection & Enrichment:** Automated async Python collectors gather "
    "raw match, event, and player records. Where tournament equipment metadata "
    "is missing, a **Gemini** vision script identifies table brands from "
    "tournament footage.\n"
    "- **Warehouse & Modelling:** Raw payloads are landed in **PostgreSQL** "
    "and transformed through **dbt** (`staging`, `intermediate`, and `marts` "
    "layers), alongside **manual CSV overrides** (local dbt seeds) for "
    "edge-case score, format, sponsor, and player-profile corrections.\n"
    "- **Analysis & Front End:** Curated warehouse marts are exported to "
    "compact **Parquet** snapshots (with senior-singles players and "
    "export-time age instead of dates of birth) and read with **Polars** in "
    "this **Streamlit** app. Skill ratings (Glicko-2) and point-level "
    "analytics pages are planned next.\n\n"
    "The current launch version was developed with AI assistance using "
    "**DeepSeek** and **Google Antigravity**, building upon an earlier "
    "prototype built by hand in Jupyter notebooks."
)

# ---------------------------------------------------------------------------
# 4. About Me & Inspiration
# ---------------------------------------------------------------------------
st.subheader("About Me / Inspiration Behind This Project")
st.markdown(
    "I picked up table tennis in London in my mid-20s and enjoy both the "
    "sport and working with data.\n In major sports such as tennis and football, "
    "there is endless public data and analysis available.\n Those same "
    "metrics and insights are not widely available for table tennis. "
    "I want to change that.\n\n"
    "If you have any questions or would like to collaborate, reach out via "
    "email at [marcusannegarn@gmail.com](mailto:marcusannegarn@gmail.com) or "
    "connect on "
    "[LinkedIn](https://www.linkedin.com/in/marcus-annegarn-5009a4195/)."
)

render_sidebar_footer(load_snapshot_timestamp())

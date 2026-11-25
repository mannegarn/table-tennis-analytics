"""Theme-aware CSS polish for Streamlit pages (works in both Light and Dark mode).

Uses Streamlit's built-in CSS variables (``--secondary-background-color``,
``--text-color``, ``--primary-color``) so cards and borders adapt
automatically when you switch between Light and Dark mode.
"""

from __future__ import annotations

import streamlit as st

APP_CSS = """
<style>
:root {
    --tt-orange: #F59E0B;
}

/* Ensure main app body and header follow the active theme background */
.stApp,
[data-testid="stAppViewContainer"],
[data-testid="stHeader"] {
    background-color: var(--background-color);
}

/* Tighten top padding while leaving room for Streamlit's top toolbar */
.block-container {
    padding-top: 3rem;
    padding-bottom: 2.5rem;
}

/* Style native st.metric widgets as subtle cards with a full orange border */
div[data-testid="stMetric"] {
    background-color: var(--secondary-background-color);
    border: 1px solid rgba(245, 158, 11, 0.55);
    border-radius: 10px;
    padding: 0.9rem 1.1rem;
}

div[data-testid="stMetricLabel"] {
    opacity: 0.78;
    font-size: 0.85rem;
}

div[data-testid="stMetricValue"] {
    font-weight: 600;
}

/* Reusable info card for First/Last event callouts and Home page cards */
.tt-card {
    background-color: var(--secondary-background-color);
    border: 1px solid rgba(245, 158, 11, 0.55);
    border-radius: 10px;
    padding: 1rem 1.2rem;
    margin-bottom: 0.75rem;
    min-height: 128px;
    transition: border-color 0.15s ease;
}

.tt-card:hover {
    border-color: var(--tt-orange);
}

/* Subtle orange pop on page-link buttons */
a[data-testid="stPageLink-NavLink"] {
    border: 1px solid rgba(245, 158, 11, 0.45);
    border-radius: 8px;
    transition: border-color 0.15s ease, background-color 0.15s ease;
}

a[data-testid="stPageLink-NavLink"]:hover {
    border-color: var(--tt-orange);
}

.tt-card-label {
    color: var(--primary-color);
    font-size: 0.78rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 0.25rem;
}

.tt-card-title {
    color: var(--text-color);
    font-size: 1.08rem;
    font-weight: 600;
    margin-bottom: 0.35rem;
}

.tt-card-meta {
    color: var(--text-color);
    opacity: 0.82;
    font-size: 0.88rem;
    line-height: 1.45;
}
</style>
"""


def apply_page_theme() -> None:
    """Inject theme-aware CSS into the active Streamlit page."""
    st.markdown(APP_CSS, unsafe_allow_html=True)


def render_sidebar_footer(snapshot_timestamp: str | None) -> None:
    """Render author credit, snapshot timestamp, and data disclaimer in the sidebar."""
    with st.sidebar:
        st.caption("**Built by Marcus Annegarn**")
        if snapshot_timestamp:
            st.caption(f"Data collected: `{snapshot_timestamp}`")
        st.caption(
            "Disclaimer: Independent, non-commercial analytics project using "
            "publicly available WTT and ITTF match records. Not affiliated with "
            "or endorsed by WTT or ITTF."
        )

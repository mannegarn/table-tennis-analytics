"""Theme-aware CSS polish and shared sidebar navigation (Light & Dark mode).

Uses Streamlit's built-in CSS variables (``--background-color``,
``--secondary-background-color``, ``--text-color``, ``--primary-color``) so
cards, borders, and navigation adapt automatically between Light and Dark mode.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import streamlit as st

APP_CSS = """
<style>
:root {
    --tt-orange: #F59E0B;
}

html {
    scroll-behavior: smooth;
}

/* Ensure main app body and header follow the active theme background */
.stApp,
[data-testid="stAppViewContainer"],
[data-testid="stHeader"] {
    background-color: var(--background-color) !important;
}

/* Tighten top padding while leaving room for Streamlit's top toolbar */
.block-container {
    padding-top: 2.75rem;
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

/* Editable placeholder write-up card (dashed accent border) */
.tt-placeholder {
    background-color: var(--secondary-background-color);
    border: 1px dashed rgba(245, 158, 11, 0.65);
    border-radius: 10px;
    padding: 0.85rem 1.1rem;
    margin-bottom: 0.9rem;
    font-size: 0.92rem;
    line-height: 1.5;
    color: var(--text-color);
}

.tt-placeholder-tag {
    color: var(--primary-color);
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    margin-bottom: 0.25rem;
}

/* Highlighted summary box (e.g. binomial & S-curve overview) */
.tt-summary-box {
    background-color: var(--secondary-background-color);
    border-left: 4px solid var(--tt-orange);
    border-top: 1px solid rgba(245, 158, 11, 0.35);
    border-right: 1px solid rgba(245, 158, 11, 0.35);
    border-bottom: 1px solid rgba(245, 158, 11, 0.35);
    border-radius: 8px;
    padding: 0.95rem 1.15rem;
    margin-bottom: 1rem;
    font-size: 0.93rem;
    line-height: 1.55;
    color: var(--text-color);
}

.tt-summary-box a {
    color: var(--primary-color);
    font-weight: 600;
    text-decoration: underline;
    text-underline-offset: 2px;
}

/* Subtle orange pop on page-link buttons */
a[data-testid="stPageLink-NavLink"] {
    border: 1px solid rgba(245, 158, 11, 0.45);
    border-radius: 8px;
    min-height: 40px;
    transition: border-color 0.15s ease, background-color 0.15s ease;
}

a[data-testid="stPageLink-NavLink"]:hover {
    border-color: var(--tt-orange);
}

/* Distinct sidebar styling: overview section label & Analytics dropdown */
.tt-nav-section-label {
    color: var(--text-color);
    opacity: 0.65;
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin: 0.2rem 0 0.4rem 0.2rem;
}

section[data-testid="stSidebar"] details {
    background-color: rgba(124, 58, 237, 0.08);
    border: 1.5px solid rgba(245, 158, 11, 0.72);
    border-radius: 10px;
    padding: 0.15rem 0.35rem 0.35rem;
    margin: 0.6rem 0 0.85rem;
}

section[data-testid="stSidebar"] details summary {
    font-weight: 700;
    color: var(--primary-color);
}

section[data-testid="stSidebar"] details a[data-testid="stPageLink-NavLink"] {
    border-color: rgba(124, 58, 237, 0.38);
    margin-top: 0.2rem;
}

section[data-testid="stSidebar"] details a[data-testid="stPageLink-NavLink"]:hover {
    border-color: var(--tt-orange);
}

/* Match st.metric and .tt-card orange outline on st.dataframe tables */
div[data-testid="stDataFrame"] {
    border: 1px solid rgba(245, 158, 11, 0.55);
    border-radius: 10px;
    overflow: hidden;
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

/* Mobile responsiveness tweaks */
@media (max-width: 768px) {
    .block-container {
        padding-top: 2.15rem;
        padding-left: 0.9rem;
        padding-right: 0.9rem;
        padding-bottom: 2rem;
    }

    h1 {
        font-size: 1.7rem !important;
    }

    h2 {
        font-size: 1.35rem !important;
    }

    h3 {
        font-size: 1.15rem !important;
    }

    div[data-testid="stMetric"] {
        padding: 0.7rem 0.85rem;
    }

    .tt-card {
        min-height: auto;
        padding: 0.85rem 1rem;
    }

    a[data-testid="stPageLink-NavLink"],
    button[kind="segmented_control"],
    button[kind="segmented_controlActive"] {
        min-height: 44px;
    }

    .katex-display {
        font-size: 0.82em;
        overflow-x: auto;
        overflow-y: hidden;
        padding-bottom: 0.25rem;
    }
}
</style>
"""


def apply_page_theme() -> None:
    """Inject theme-aware CSS into the active Streamlit page."""
    st.markdown(APP_CSS, unsafe_allow_html=True)


def ensure_standalone_shell(snapshot_timestamp: str | None = None) -> None:
    """Apply theme CSS if a page script is run directly outside the Home router."""
    if not st.session_state.get("_tt_router_active", False):
        apply_page_theme()
        if snapshot_timestamp is not None:
            render_sidebar_footer(snapshot_timestamp)


def render_sidebar_nav(
    overview_pages: Sequence[Any],
    analytics_pages: Sequence[Any],
    snapshot_timestamp: str | None,
) -> None:
    """Render grouped left-sidebar navigation with a collapsible Analytics menu."""
    with st.sidebar:
        st.markdown(
            '<div class="tt-nav-section-label">Overview</div>',
            unsafe_allow_html=True,
        )
        for page in overview_pages:
            st.page_link(page, label=page.title, icon=page.icon)

        with st.expander("📈 Analytics", expanded=True):
            for page in analytics_pages:
                st.page_link(page, label=page.title, icon=page.icon)

        st.divider()
    render_sidebar_footer(snapshot_timestamp)


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

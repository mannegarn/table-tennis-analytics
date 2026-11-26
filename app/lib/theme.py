"""Theme-aware CSS polish and shared sidebar navigation (Light & Dark mode).

Uses Streamlit's built-in CSS variables (``--background-color``,
``--secondary-background-color``, ``--text-color``, ``--primary-color``) so
cards, borders, and navigation adapt automatically between Light and Dark mode.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import streamlit as st

GITHUB_REPO_URL = "https://github.com/mannegarn/table-tennis-analytics"
LINKEDIN_URL = "https://www.linkedin.com/in/marcus-annegarn-5009a4195/"
CONTACT_EMAIL = "marcusannegarn@gmail.com"

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

/* Leave safe clearance for Streamlit Cloud's top-right toolbar and bottom badge */
.block-container {
    padding-top: 3.4rem;
    padding-bottom: 4.25rem;
}

/* Style native st.metric widgets as subtle cards with a full orange border */
div[data-testid="stMetric"] {
    background-color: var(--secondary-background-color);
    border: 1px solid rgba(245, 158, 11, 0.55);
    border-radius: 10px;
    padding: 0.9rem 1.05rem;
    height: 100%;
}

div[data-testid="stMetricLabel"],
div[data-testid="stMetricLabel"] *,
div[data-testid="stMetricLabel"] > div,
div[data-testid="stMetricLabel"] [data-testid="stMarkdownContainer"],
div[data-testid="stMetricLabel"] [data-testid="stMarkdownContainer"] p {
    opacity: 0.82;
    font-size: 0.82rem;
    line-height: 1.25;
    height: auto !important;
    white-space: normal !important;
    overflow: visible !important;
    text-overflow: clip !important;
    flex-wrap: wrap !important;
}

div[data-testid="stMetricValue"] {
    font-weight: 700;
    font-size: clamp(1.3rem, 2.1vw, 1.95rem);
    line-height: 1.15;
}

/* Prevent toggle & checkbox labels from clipping mid-word on split-screen widths */
div[data-testid="stCheckbox"] label p,
div[data-testid="stWidgetLabel"] p {
    white-space: normal !important;
    overflow: visible !important;
    text-overflow: unset !important;
}

/* Tech stack badge strip */
.tt-pill-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.45rem;
    margin: 0.55rem 0 0.85rem;
}

.tt-pill {
    display: inline-flex;
    align-items: center;
    background-color: var(--secondary-background-color);
    border: 1px solid rgba(167, 139, 250, 0.42);
    border-radius: 999px;
    padding: 0.22rem 0.68rem;
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--text-color);
    letter-spacing: 0.01em;
    white-space: nowrap;
}

/* Reusable info card for First/Last event callouts and Home page cards */
.tt-card {
    background-color: var(--secondary-background-color);
    border: 1px solid rgba(245, 158, 11, 0.55);
    border-radius: 10px;
    padding: 1rem 1.2rem;
    margin-bottom: 0.55rem;
    min-height: 122px;
    transition: border-color 0.15s ease;
}

.tt-card:hover {
    border-color: var(--tt-orange);
}

/* Headline discovery cards on Home */
.tt-discovery-card {
    background-color: var(--secondary-background-color);
    border: 1px solid rgba(245, 158, 11, 0.45);
    border-left: 4px solid var(--tt-orange);
    border-radius: 10px;
    padding: 0.95rem 1.15rem;
    margin-bottom: 0.65rem;
    min-height: 178px;
}

.tt-discovery-stat {
    color: var(--tt-orange);
    font-size: clamp(1.32rem, 2vw, 1.72rem);
    font-weight: 700;
    line-height: 1.15;
    margin-bottom: 0.3rem;
}

.tt-discovery-title {
    color: var(--text-color);
    font-size: 0.98rem;
    font-weight: 600;
    margin-bottom: 0.3rem;
}

.tt-discovery-body {
    color: var(--text-color);
    opacity: 0.84;
    font-size: 0.86rem;
    line-height: 1.45;
}

/* 4-stage pipeline architecture grid on About */
.tt-pipeline-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 0.75rem;
    margin: 0.75rem 0 1.15rem;
}

.tt-pipeline-step {
    background-color: var(--secondary-background-color);
    border: 1px solid rgba(245, 158, 11, 0.45);
    border-radius: 10px;
    padding: 0.85rem 1rem;
}

.tt-pipeline-num {
    color: var(--primary-color);
    font-size: 0.74rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 0.25rem;
}

.tt-pipeline-title {
    color: var(--text-color);
    font-size: 0.96rem;
    font-weight: 600;
    margin-bottom: 0.3rem;
}

.tt-pipeline-desc {
    color: var(--text-color);
    opacity: 0.84;
    font-size: 0.84rem;
    line-height: 1.42;
}

/* Quick-jump anchor bar on long analytics pages */
.tt-jump-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 0.45rem;
    margin: 0.35rem 0 0.85rem;
}

.tt-jump-bar a {
    display: inline-block;
    background-color: var(--secondary-background-color);
    border: 1px solid rgba(245, 158, 11, 0.45);
    border-radius: 999px;
    padding: 0.25rem 0.72rem;
    font-size: 0.82rem;
    font-weight: 600;
    color: var(--text-color);
    text-decoration: none;
    transition: border-color 0.15s ease;
}

.tt-jump-bar a:hover {
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

/* Polished editorial insight card for analytics sections */
.tt-insight-card {
    background-color: var(--secondary-background-color);
    border: 1px solid rgba(245, 158, 11, 0.45);
    border-radius: 10px;
    padding: 0.9rem 1.15rem;
    margin-bottom: 0.95rem;
    font-size: 0.93rem;
    line-height: 1.55;
    color: var(--text-color);
}

.tt-insight-card ul {
    margin: 0;
    padding-left: 1.2rem;
}

.tt-insight-card li {
    margin-bottom: 0.65rem;
}

.tt-insight-card li:last-child {
    margin-bottom: 0;
}

.tt-insight-card ul ul {
    margin-top: 0.35rem;
    margin-bottom: 0.15rem;
    padding-left: 1.2rem;
}

.tt-insight-card ul ul li {
    margin-bottom: 0.3rem;
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

.tt-summary-box li {
    margin-bottom: 0.45rem;
}

.tt-summary-box li:last-child {
    margin-bottom: 0;
}

.tt-summary-box a {
    color: var(--primary-color);
    font-weight: 600;
    text-decoration: underline;
    text-underline-offset: 2px;
}

/* Subtle orange pop on page-link and link-button CTAs */
a[data-testid="stPageLink-NavLink"],
div[data-testid="stLinkButton"] a {
    border: 1px solid rgba(245, 158, 11, 0.45);
    border-radius: 8px;
    min-height: 40px;
    display: inline-flex;
    align-items: center;
    transition: border-color 0.15s ease, background-color 0.15s ease;
}

a[data-testid="stPageLink-NavLink"]:hover,
div[data-testid="stLinkButton"] a:hover {
    border-color: var(--tt-orange);
}

/* Distinct sidebar styling: clean nav links without heavy nested box clutter */
.tt-nav-section-label {
    color: var(--text-color);
    opacity: 0.65;
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin: 0.1rem 0 0.35rem 0.2rem;
}

section[data-testid="stSidebar"] a[data-testid="stPageLink-NavLink"] {
    border: 1px solid rgba(167, 139, 250, 0.22);
    min-height: 36px;
    padding-top: 0.22rem;
    padding-bottom: 0.22rem;
}

section[data-testid="stSidebar"] a[data-testid="stPageLink-NavLink"]:hover {
    border-color: var(--tt-orange);
    background-color: rgba(245, 158, 11, 0.08);
}

section[data-testid="stSidebar"]
a[data-testid="stPageLink-NavLink"][aria-current="page"] {
    border-color: rgba(245, 158, 11, 0.78);
    background-color: rgba(124, 58, 237, 0.16);
}

section[data-testid="stSidebar"] details {
    background-color: rgba(124, 58, 237, 0.08);
    border: 1px solid rgba(245, 158, 11, 0.62);
    border-radius: 10px;
    padding: 0.1rem 0.35rem 0.3rem;
    margin: 0.45rem 0 0.7rem;
}

section[data-testid="stSidebar"] details summary {
    font-weight: 700;
    color: var(--primary-color);
}

section[data-testid="stSidebar"] details a[data-testid="stPageLink-NavLink"] {
    margin-top: 0.18rem;
}

/* Match st.metric and .tt-card orange outline on st.dataframe tables */
div[data-testid="stDataFrame"] {
    border: 1px solid rgba(245, 158, 11, 0.55);
    border-radius: 10px;
    overflow: hidden;
}

.tt-card-label {
    color: var(--primary-color);
    font-size: 0.75rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 0.25rem;
    white-space: nowrap;
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

@media (max-width: 1024px) {
    .tt-pipeline-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    div[data-testid="stMetric"] {
        padding: 0.72rem 0.8rem;
    }

    .tt-discovery-card {
        min-height: 185px;
    }
}

/* Mobile responsiveness tweaks */
@media (max-width: 768px) {
    .block-container {
        padding-top: 2.35rem;
        padding-left: 0.9rem;
        padding-right: 0.9rem;
        padding-bottom: 3.5rem;
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

    .tt-card,
    .tt-discovery-card {
        min-height: auto;
        padding: 0.85rem 1rem;
    }

    .tt-pipeline-grid {
        grid-template-columns: 1fr;
    }

    a[data-testid="stPageLink-NavLink"],
    div[data-testid="stLinkButton"] a,
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
    """Render author credit, portfolio links, snapshot timestamp, and disclaimer."""
    with st.sidebar:
        st.caption(
            f"**Built by Marcus Annegarn**  \n"
            f"[GitHub]({GITHUB_REPO_URL}) · [LinkedIn]({LINKEDIN_URL})"
        )
        if snapshot_timestamp:
            st.caption(f"Data collected: `{snapshot_timestamp}`")
        st.caption(
            "Disclaimer: Independent, non-commercial analytics project using "
            "publicly available WTT and ITTF match records. Not affiliated with "
            "or endorsed by WTT or ITTF."
        )

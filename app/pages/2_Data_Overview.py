"""Dataset & Warehouse Summary Page.

Displays:
  - Event counts and date ranges
  - Match counts (Men's vs Women's, Best-of-5 vs Best-of-7, Senior Singles vs Catalog)
  - Player counts, distinct countries/associations, and age range
  - First and last events with matches (joined to dim_events)
  - Yearly match volume chart
"""

from __future__ import annotations

from html import escape

import plotly.express as px
import polars as pl
import streamlit as st
from lib.charts import FORMAT_COLORS, GENDER_COLORS, render_chart, style_figure
from lib.data import (
    EventBoundary,
    build_yearly_matches_table,
    compute_summary_stats,
    data_files_exist,
    load_catalog_rollup,
    load_events,
    load_players,
    load_senior_matches,
    load_snapshot_timestamp,
)
from lib.theme import ensure_standalone_shell

ensure_standalone_shell(load_snapshot_timestamp())

st.title("Data Overview")
st.caption(
    "Summary of the tournaments, matches, and players in this app. All analytics "
    "pages cover senior singles matches (including singles matches inside team "
    "events) from WTT and ITTF tournaments. Doubles, youth, and para events are "
    "not included yet, and domestic leagues are planned."
)

if not data_files_exist():
    st.error(
        "Parquet snapshots were not found in `app/data/`. "
        "Run `make app-refresh` (or `uv run tt-app-export`) with PostgreSQL running, "
        "then refresh this page."
    )
    st.stop()

# Load cached Parquet tables and compute summary metrics
events_df = load_events()
players_df = load_players()
senior_matches_df = load_senior_matches()
catalog_rollup_df = load_catalog_rollup()

stats = compute_summary_stats(
    events_df,
    players_df,
    senior_matches_df,
    catalog_rollup_df,
)

# ---------------------------------------------------------------------------
# 1. Top-level KPIs
# ---------------------------------------------------------------------------
st.subheader("Overview")
k1, k2, k3 = st.columns(3)

k1.metric(
    "Active Events",
    f"{stats.events_with_senior_singles:,}",
    help=(
        f"Events with senior singles matches. Total events in catalog: "
        f"{stats.total_events:,}. Events with any catalog match: "
        f"{stats.events_with_catalog_matches:,}."
    ),
)
k2.metric(
    "Matches",
    f"{stats.senior_total_matches:,}",
    help=(
        "Senior singles matches. Full match catalog (all disciplines, youth, "
        f"para, team rubbers): {stats.catalog_total_matches:,}."
    ),
)
k3.metric(
    "Players",
    f"{stats.total_players:,}",
)

k4, k5 = st.columns(2)
k4.metric(
    "Countries / Associations",
    f"{stats.distinct_countries:,}",
)
age_label = (
    f"{stats.min_age} - {stats.max_age} yrs"
    if stats.min_age is not None and stats.max_age is not None
    else "N/A"
)
k5.metric(
    "Player Age Range",
    age_label,
    help=(
        f"Age at snapshot export, based on {stats.players_with_age:,} players "
        "with known age."
        if stats.players_with_age > 0
        else None
    ),
)

st.divider()

# ---------------------------------------------------------------------------
# 1b. Men vs Women Table (Players & Matches)
# ---------------------------------------------------------------------------
st.subheader("Players and Matches by Gender")

gender_summary_df = pl.DataFrame(
    {
        "Category": ["Men", "Women", "Total"],
        "Players": [
            f"{stats.mens_players:,}",
            f"{stats.womens_players:,}",
            f"{stats.total_players:,}",
        ],
        "Matches (Senior Singles)": [
            f"{stats.senior_mens_matches:,}",
            f"{stats.senior_womens_matches:,}",
            f"{stats.senior_total_matches:,}",
        ],
    }
)
st.dataframe(gender_summary_df.to_pandas(), hide_index=True, width="stretch")

unknown_gender_players = stats.total_players - stats.mens_players - stats.womens_players
if unknown_gender_players > 0:
    st.caption(
        f"Note: {unknown_gender_players:,} players in the catalog have an "
        "unspecified gender, so Men + Women is slightly less than Total."
    )
st.caption(
    "Men's match counts are higher because open-entry tournaments (especially "
    "WTT Feeder and Contender events) attract larger men's fields, so there "
    "are more qualifying and group-stage matches."
)

st.divider()

# ---------------------------------------------------------------------------
# 2. First & Last Events with Matches + Date Ranges
# ---------------------------------------------------------------------------
st.subheader("Tournament Date Ranges")


def render_event_card(label: str, boundary: EventBoundary | None) -> None:
    """Render a dark-mode summary card for the earliest or latest event."""
    if boundary is None:
        st.info(f"No data for {label}.")
        return
    name = escape(boundary.event_name)
    country = escape(boundary.country or "Unknown location")
    if boundary.start_date is not None:
        end = boundary.end_date or boundary.start_date
        date_span = f"{boundary.start_date:%d/%m/%y} to {end:%d/%m/%y}"
    else:
        date_span = "Unknown"
    st.markdown(
        f"""
        <div class="tt-card">
            <div class="tt-card-label">{escape(label)}</div>
            <div class="tt-card-title">{name}</div>
            <div class="tt-card-meta">
                <strong>Dates:</strong> {escape(date_span)}<br>
                <strong>Location:</strong> {country}<br>
                <strong>Senior Singles Matches:</strong> {boundary.match_count:,}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


c_first, c_last = st.columns(2)
with c_first:
    render_event_card(
        "First Event with Senior Singles Matches", stats.first_senior_event
    )
with c_last:
    render_event_card("Last Event with Senior Singles Matches", stats.last_senior_event)

st.divider()

# ---------------------------------------------------------------------------
# 3. Gender & Match Format Breakdown (Senior Singles)
# ---------------------------------------------------------------------------
st.subheader("Matches by Gender and Format")

col_gender, col_format = st.columns(2)

with col_gender:
    gender_df = pl.DataFrame(
        {
            "Category": ["Men", "Women"],
            "Matches": [stats.senior_mens_matches, stats.senior_womens_matches],
        }
    )
    gender_max = max(stats.senior_mens_matches, stats.senior_womens_matches, 1)
    fig_gender = px.bar(
        gender_df,
        x="Category",
        y="Matches",
        color="Category",
        color_discrete_map=GENDER_COLORS,
        text="Matches",
        title="Men's vs Women's Matches",
    )
    fig_gender.update_traces(texttemplate="%{text:,}", textposition="outside")
    fig_gender.update_layout(showlegend=False)
    fig_gender.update_yaxes(range=[0, gender_max * 1.15])
    fig_gender = style_figure(fig_gender, height=340)
    render_chart(fig_gender, key="overview_gender_chart")

with col_format:
    format_df = pl.DataFrame(
        {
            "Format": ["Best of 5", "Best of 7", "Other"],
            "Matches": [
                stats.senior_bo5_matches,
                stats.senior_bo7_matches,
                stats.senior_other_format_matches,
            ],
        }
    ).filter(pl.col("Matches") > 0)
    format_max = max(
        stats.senior_bo5_matches,
        stats.senior_bo7_matches,
        stats.senior_other_format_matches,
        1,
    )
    format_title = (
        "Senior Singles Match Formats (Bo5, Bo7 & Other)"
        if stats.senior_other_format_matches > 0
        else "Best-of-5 vs Best-of-7 Matches"
    )
    fig_format = px.bar(
        format_df,
        x="Format",
        y="Matches",
        color="Format",
        color_discrete_map=FORMAT_COLORS,
        text="Matches",
        title=format_title,
    )
    fig_format.update_traces(texttemplate="%{text:,}", textposition="outside")
    fig_format.update_layout(showlegend=False)
    fig_format.update_yaxes(range=[0, format_max * 1.15])
    fig_format = style_figure(fig_format, height=340)
    render_chart(fig_format, key="overview_format_chart")

st.divider()

# ---------------------------------------------------------------------------
# 4. Yearly Timeline
# ---------------------------------------------------------------------------
st.subheader("Matches by Year")

yearly_df = build_yearly_matches_table(events_df, senior_matches_df)
if yearly_df.is_empty():
    st.info("No yearly match data available.")
else:
    fig_yearly = px.bar(
        yearly_df,
        x="event_year",
        y="Matches",
        color="Gender",
        color_discrete_map=GENDER_COLORS,
        barmode="stack",
        title="Senior Singles Matches per Year",
        labels={"event_year": "Year"},
    )
    fig_yearly = style_figure(fig_yearly, height=380)
    render_chart(fig_yearly, key="overview_yearly_chart")

st.divider()

with st.expander("Methodology & Data Notes", expanded=False):
    st.markdown(
        f"- **Senior singles vs. full catalog:** Analytics pages use the "
        f"**{stats.senior_total_matches:,}** senior singles matches across "
        f"**{stats.events_with_senior_singles:,}** events. The broader WTT/ITTF "
        f"catalog holds **{stats.catalog_total_matches:,}** total match records "
        f"across all disciplines ({stats.total_events:,} events total, "
        f"{stats.events_with_catalog_matches:,} with match records).\n"
        f"- **Calendar span:** Catalog event metadata runs from "
        f"`{stats.catalog_first_event_date}` to `{stats.catalog_last_event_date}` "
        "(including historical ITTF archive entries back to 1926 and scheduled "
        "events into 2027), while detailed senior singles match records start "
        "from 2021 onward.\n"
        "- **Player age privacy:** Ages reflect each player's age at snapshot "
        "export time; raw dates of birth are not exported."
    )
    if stats.first_catalog_event and stats.last_catalog_event:
        first_cat = stats.first_catalog_event
        last_cat = stats.last_catalog_event
        first_word = "match" if first_cat.match_count == 1 else "matches"
        last_word = "match" if last_cat.match_count == 1 else "matches"
        st.markdown(
            f"- **First catalog event with matches:** {first_cat.event_name} "
            f"(`{first_cat.start_date}`, {first_cat.match_count:,} {first_word})\n"
            f"- **Last catalog event with matches:** {last_cat.event_name} "
            f"(`{last_cat.start_date}`, {last_cat.match_count:,} {last_word})"
        )

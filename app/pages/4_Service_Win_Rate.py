"""Service Win Rate Analytics Page.

Showcases player-level Total, Serve, and Receive point win rates from
``marts.fct_match_commentary`` across completed, played senior singles matches
with complete point-by-point commentary (pooling Best of 5 and Best of 7).
"""

from __future__ import annotations

from typing import Any, cast

import polars as pl
import streamlit as st
from lib.charts import render_chart
from lib.data import (
    data_files_exist,
    load_serve_receive_stats,
    load_snapshot_timestamp,
)
from lib.serve_receive import (
    COMPARISON_LABELS,
    MetricView,
    ServeReceiveChartSummary,
    build_player_table_view,
    build_serve_receive_scatter,
    compute_global_kpis,
    enrich_serve_receive_stats,
    filter_serve_receive_rows,
)
from lib.theme import ensure_standalone_shell

ensure_standalone_shell(load_snapshot_timestamp())


@st.cache_data(show_spinner=False)
def _cached_serve_receive_table(raw_height: int) -> pl.DataFrame:
    """Enrich and cache per-player serve/receive metrics."""
    _ = raw_height
    return enrich_serve_receive_stats(load_serve_receive_stats())


def _render_insight_card(bullets_html: list[str]) -> None:
    """Render a clean editorial insight card with structured bullet points."""
    items = "".join(f"<li>{item}</li>" for item in bullets_html)
    st.markdown(
        f'<div class="tt-insight-card"><ul>{items}</ul></div>',
        unsafe_allow_html=True,
    )


def _render_chart_caption(summary: ServeReceiveChartSummary) -> None:
    """Render a compact summary caption beneath the Serve/Receive scatter plot."""
    if summary.n_players == 0:
        return

    parts: list[str] = [
        f"**{summary.n_players:,}** players "
        f"({summary.n_attackers:,} Attackers, {summary.n_defenders:,} Defenders)"
    ]
    if summary.mean_delta_pp is not None:
        parts.append(f"Mean delta vs Total: `{summary.mean_delta_pp:+.2f} pp`")
    if summary.attacker_mean_delta_pp is not None:
        parts.append(f"Attackers: `{summary.attacker_mean_delta_pp:+.2f} pp`")
    if summary.defender_mean_delta_pp is not None:
        parts.append(f"Defenders: `{summary.defender_mean_delta_pp:+.2f} pp`")
    if summary.mean_serve_minus_receive_pp is not None:
        parts.append(
            f"Avg Serve - Receive gap: `{summary.mean_serve_minus_receive_pp:+.2f} pp`"
        )
    st.caption(" · ".join(parts))


def _table_column_config(metric_view: MetricView) -> dict[str, Any]:
    """Return clean Streamlit column formatting for the leaderboard table.

    Uses compact headers and explicit pixel widths when ``metric_view == "All"``
    so all 15 columns fit comfortably on desktop viewports without clipping.
    """
    compact = metric_view == "All"
    num_w: int | str = 52 if compact else "small"
    pct_w: int | str = 58 if compact else "small"
    return {
        "Player": st.column_config.TextColumn(
            "Player",
            width=128 if compact else "medium",
            pinned=True,
        ),
        "Org": st.column_config.TextColumn("Org", width=44 if compact else "small"),
        "Style": st.column_config.TextColumn("Style", width=66 if compact else "small"),
        "Matches": st.column_config.NumberColumn(
            "Matches",
            format="%d",
            width=56 if compact else "small",
            help="Completed played senior singles matches with full commentary",
        ),
        "Total Won": st.column_config.NumberColumn(
            "Tot W" if compact else "Total Won",
            format="%d",
            width=num_w,
            help="Total points won across commentary-covered matches",
        ),
        "Total Lost": st.column_config.NumberColumn(
            "Tot L" if compact else "Total Lost",
            format="%d",
            width=num_w,
            help="Total points lost across commentary-covered matches",
        ),
        "Total Win %": st.column_config.NumberColumn(
            "Tot %" if compact else "Total Win %",
            format="%.2f%%",
            width=pct_w,
            help="Overall point win percentage",
        ),
        "Serve Won": st.column_config.NumberColumn(
            "Srv W" if compact else "Serve Won",
            format="%d",
            width=num_w,
            help="Points won while serving",
        ),
        "Serve Lost": st.column_config.NumberColumn(
            "Srv L" if compact else "Serve Lost",
            format="%d",
            width=num_w,
            help="Points lost while serving",
        ),
        "Serve Win %": st.column_config.NumberColumn(
            "Srv %" if compact else "Serve Win %",
            format="%.2f%%",
            width=pct_w,
            help="Point win percentage when serving",
        ),
        "Serve Delta": st.column_config.NumberColumn(
            "Srv Δ" if compact else "Serve Delta (pp)",
            format="%+.2f pp",
            width=64 if compact else "small",
            help=(
                "Serve Win % minus the player's Total Point Win % "
                "(positive = stronger on serve than overall average)."
            ),
        ),
        "Receive Won": st.column_config.NumberColumn(
            "Rcv W" if compact else "Receive Won",
            format="%d",
            width=num_w,
            help="Points won while receiving",
        ),
        "Receive Lost": st.column_config.NumberColumn(
            "Rcv L" if compact else "Receive Lost",
            format="%d",
            width=num_w,
            help="Points lost while receiving",
        ),
        "Receive Win %": st.column_config.NumberColumn(
            "Rcv %" if compact else "Receive Win %",
            format="%.2f%%",
            width=pct_w,
            help="Point win percentage when receiving",
        ),
        "Receive Delta": st.column_config.NumberColumn(
            "Rcv Δ" if compact else "Receive Delta (pp)",
            format="%+.2f pp",
            width=64 if compact else "small",
            help=(
                "Receive Win % minus the player's Total Point Win % "
                "(shows relative receive advantage or disadvantage)."
            ),
        ),
    }


# ---------------------------------------------------------------------------
# Page Header & Scope Note
# ---------------------------------------------------------------------------
st.title("🎯 Service Win Rate")
st.info(
    "**Scope note:** Not all matches in the warehouse have full point-by-point "
    "commentary data. This page covers only completed, played senior singles "
    "matches with complete commentary (`PT`, `SSR`, `DPT`) and an identifiable "
    "server (Best of 5 and Best of 7 pooled). The **Matches** column reflects "
    "only those commentary-covered matches."
)

if not data_files_exist():
    st.error(
        "Parquet snapshots were not found in `app/data/`. "
        "Run `make app-refresh` (or `uv run tt-app-export`) with PostgreSQL running, "
        "then refresh this page."
    )
    st.stop()

raw_sr_df = load_serve_receive_stats()
sr_df = _cached_serve_receive_table(raw_sr_df.height)
kpis = compute_global_kpis(sr_df)

# ---------------------------------------------------------------------------
# Global KPIs & Summary Box
# ---------------------------------------------------------------------------
g1, g2, g3 = st.columns(3)
g1.metric(
    "Global Serve Win %",
    (
        f"{kpis.global_serve_win_pct:.2f}%"
        if kpis.global_serve_win_pct is not None
        else "N/A"
    ),
    help=f"Across {kpis.total_points:,} points in {kpis.total_matches:,} matches",
)
g2.metric(
    "Men's Global Serve Win %",
    f"{kpis.mens_serve_win_pct:.2f}%" if kpis.mens_serve_win_pct is not None else "N/A",
    help=f"{kpis.mens_points:,} points across {kpis.mens_matches:,} men's matches",
)
g3.metric(
    "Women's Global Serve Win %",
    (
        f"{kpis.womens_serve_win_pct:.2f}%"
        if kpis.womens_serve_win_pct is not None
        else "N/A"
    ),
    help=(
        f"{kpis.womens_points:,} points across {kpis.womens_matches:,} women's matches"
    ),
)

k4, k5, k6 = st.columns(3)
k4.metric(
    "Global Receive Win %",
    (
        f"{kpis.global_receive_win_pct:.2f}%"
        if kpis.global_receive_win_pct is not None
        else "N/A"
    ),
)
k5.metric("Commentary Matches", f"{kpis.total_matches:,}")
k6.metric("Points Analyzed", f"{kpis.total_points:,}")

st.markdown(
    """
    <div class="tt-summary-box">
        <strong>How to read this page:</strong>
        <ul>
            <li>
                <strong>Total / Serve / Receive Win % &amp; Delta:</strong> Each
                player's points won, lost, and win percentage overall, when
                serving, and when receiving. <strong>Serve Delta</strong>
                (<code>Serve Win % &minus; Total Win %</code>) and
                <strong>Receive Delta</strong>
                (<code>Receive Win % &minus; Total Win %</code>) highlight who
                gains or loses the most on serve vs receive. Use
                <strong>Metric View</strong> below to switch between
                <em>All</em>, <em>Total</em>, <em>Serve</em>, or <em>Receive</em>
                columns (ideal on narrower screens).
            </li>
            <li>
                <strong>Server advantage:</strong> Because every point has one
                server and one receiver, tour-wide
                <strong>Serve Win % + Receive Win % = 100%</strong>. Holding a
                ~53.3% serve win rate gives servers roughly a <strong>+6.7 pp</strong>
                edge over receiving.
            </li>
            <li>
                <strong>Best of 5 &amp; Best of 7 pooled:</strong> Unlike match
                win rates, per-point serve and receive probabilities are
                format-invariant, so Bo5 and Bo7 matches are combined.
            </li>
        </ul>
        <a href="#serve-receive-addendum">
            Jump to commentary parsing &amp; server inference methodology &darr;
        </a>
    </div>
    """,
    unsafe_allow_html=True,
)

st.divider()

# ---------------------------------------------------------------------------
# Section 1 (Primary): Scrollable & Sortable Player Leaderboard
# ---------------------------------------------------------------------------
st.subheader("1. Player Service & Receive Leaderboard")

available_styles = sorted(sr_df["Style"].unique().to_list())
available_grips = sorted(sr_df["Grip"].unique().to_list())
available_hands = sorted(sr_df["Hand"].unique().to_list())

f1, f2 = st.columns(2)
with f1:
    table_gender = (
        st.segmented_control(
            "Gender",
            options=["All", "Men", "Women"],
            default="All",
            key="sr_table_gender",
        )
        or "All"
    )
with f2:
    metric_view_raw = (
        st.segmented_control(
            "Metric View",
            options=["All", "Total", "Serve", "Receive"],
            default="All",
            key="sr_table_view",
            help="Switch visible stat columns and default sort column.",
        )
        or "All"
    )
    metric_view = cast(MetricView, metric_view_raw)

c_min, c_search = st.columns([1.4, 1.2], vertical_alignment="bottom")
with c_min:
    table_min_matches = st.slider(
        "Minimum matches (with commentary)",
        min_value=0,
        max_value=100,
        value=10,
        step=5,
        key="sr_table_min_matches",
    )
with c_search:
    search_query = st.text_input(
        "Search player",
        value="",
        placeholder="Filter by player name (e.g. HARIMOTO)...",
        key="sr_table_search",
    )

with st.expander(
    "Player Profile Filters (Style, Grip, Hand)",
    expanded=False,
):
    pf1, pf2, pf3 = st.columns(3)
    with pf1:
        selected_styles = st.multiselect(
            "Playing Style",
            options=available_styles,
            default=available_styles,
            key="sr_styles",
        )
    with pf2:
        selected_grips = st.multiselect(
            "Grip",
            options=available_grips,
            default=available_grips,
            key="sr_grips",
        )
    with pf3:
        selected_hands = st.multiselect(
            "Playing Hand",
            options=available_hands,
            default=available_hands,
            key="sr_hands",
        )

filtered_table_source = filter_serve_receive_rows(
    sr_df,
    gender=table_gender,
    min_matches=table_min_matches,
    search_query=search_query,
    styles=selected_styles,
    grips=selected_grips,
    hands=selected_hands,
)
table_view_df = build_player_table_view(
    filtered_table_source,
    metric_view=metric_view,
)

sort_col_label = (
    "Serve Win %" if metric_view in ("All", "Serve") else f"{metric_view} Win %"
)
st.caption(
    f"Showing **{table_view_df.height:,}** players with at least "
    f"**{table_min_matches}** commentary-covered matches "
    f"(sorted by **{sort_col_label}**; "
    "click any column header—including **Serve Delta** / **Receive Delta**—to "
    "re-sort)."
)

if table_view_df.is_empty():
    st.info("No players match the current filter and search criteria.")
else:
    st.dataframe(
        table_view_df.to_pandas(),
        key=f"sr_leaderboard_{metric_view}",
        width="stretch",
        hide_index=True,
        height=520,
        column_config=_table_column_config(metric_view),
    )

st.divider()

# ---------------------------------------------------------------------------
# Section 2 (Secondary): Serve / Receive vs Total Point % Chart
# ---------------------------------------------------------------------------
st.subheader("2. Serve & Receive vs Total Point Win %")

_render_insight_card(
    [
        (
            "<strong>On paper:</strong> In an equal-skill rally with no server "
            "edge, Serve Win % and Receive Win % would both sit right on the "
            "dashed <code>y = x</code> parity line (and around the dotted 50% "
            "crosshairs)."
        ),
        (
            "<strong>What the data shows:</strong> Serving gives a clear, "
            "consistent edge across the tour: <strong>53.34%</strong> global "
            "serve win rate vs <strong>46.66%</strong> on receive (Men: "
            "<code>53.37%</code>, Women: <code>53.29%</code>). Across players "
            "with 10+ commentary matches, <strong>Serve Win %</strong> sits an "
            "average of <code>+3.32 pp</code> <em>above</em> Total Point % while "
            "<strong>Receive Win %</strong> sits <code>&minus;3.32 pp</code> "
            "<em>below</em> it — a <strong>+6.64 pp</strong> serve-over-receive "
            "gap (<code>+6.79 pp</code> for Men vs <code>+6.45 pp</code> for "
            "Women). Notably, <strong>Defenders</strong> show a tighter split "
            "(<code>+5.59 pp</code>) than <strong>Attackers</strong> "
            "(<code>+6.68 pp</code>)."
        ),
        (
            "<strong>Why:</strong> In table tennis, the server controls the "
            "spin, placement, and length of the first ball and sets up an "
            "aggressive third-ball attack — especially on the men's tour. "
            "Choppers/defenders rely less on quick third-ball winners and "
            "neutralize opponents' serves more consistently, compressing the gap "
            "between their serve and receive win rates."
        ),
    ]
)

c_gender, c_comp = st.columns(2)
with c_gender:
    chart_gender = (
        st.segmented_control(
            "Gender",
            options=["All", "Men", "Women"],
            default="All",
            key="sr_chart_gender",
        )
        or "All"
    )
with c_comp:
    comp_label = (
        st.segmented_control(
            "Comparison",
            options=list(COMPARISON_LABELS.keys()),
            default="Serve % vs Total %",
            key="sr_chart_comparison",
        )
        or "Serve % vs Total %"
    )
    comparison = COMPARISON_LABELS[comp_label]

c_chart_min, c_chart_hl = st.columns([1.4, 1.2], vertical_alignment="bottom")
with c_chart_min:
    chart_min_matches = st.slider(
        "Minimum matches (with commentary)",
        min_value=0,
        max_value=100,
        value=10,
        step=5,
        key="sr_chart_min_matches",
    )

filtered_chart_df = filter_serve_receive_rows(
    sr_df,
    gender=chart_gender,
    min_matches=chart_min_matches,
    styles=selected_styles,
    grips=selected_grips,
    hands=selected_hands,
)

chart_player_names = sorted(filtered_chart_df["Player"].unique().to_list())
with c_chart_hl:
    sr_highlight_choice = st.selectbox(
        "Highlight a player on scatter plot",
        options=["None", *chart_player_names],
        index=0,
        key="sr_chart_highlight_player",
        help="Spot any visible player as a red star on the scatter plot.",
    )
sr_highlight_player: str | None = (
    None if sr_highlight_choice == "None" else sr_highlight_choice
)

chart_title = (
    "Serve Win % vs Total Point Win %"
    if comparison == "serve_vs_total"
    else "Receive Win % vs Total Point Win %"
)
fig, chart_summary = build_serve_receive_scatter(
    filtered_chart_df,
    comparison=comparison,
    title=chart_title,
    gender_label=chart_gender,
    highlight_player=sr_highlight_player,
)
render_chart(fig, key="sr_scatter_chart")
_render_chart_caption(chart_summary)

st.divider()

# ---------------------------------------------------------------------------
# Bottom Methodology Addendum
# ---------------------------------------------------------------------------
st.markdown('<div id="serve-receive-addendum"></div>', unsafe_allow_html=True)
st.subheader("Methodology Addendum: Commentary Parsing & Server Inference")

with st.expander(
    "How PT, SSR, DPT deletions, and opening-server inference work",
    expanded=False,
):
    st.markdown(
        "Point-level counts are pre-aggregated from the curated match commentary "
        "feed joined to completed, played senior singles matches (excluding "
        "walkovers, injuries, retirements, and defaults) during snapshot export:"
    )
    st.markdown(
        "1. **Action types parsed (`PT`, `SSR`, `DPT`):**\n"
        "   - **`PT`** (`Point won by <PLAYER>, ...`): credits one point won to "
        "the named player (and lost to the opponent) under the active server.\n"
        "   - **`SSR`** (`Service change: <PLAYER> to serve`): updates the "
        "active server. When the commentary records `UNKNOWN PLAYER`, the "
        "active server flips (`1 ↔ 2`).\n"
        "   - **`DPT`** (deleted point): cancels the most recent non-deleted "
        "`PT` event in that match using a per-match LIFO stack, matching "
        "umpire score corrections.\n"
        "2. **Opening-server inference:** Thousands of matches log their first "
        "`PT` before the first `SSR` change. For each match, we scan forward to "
        "the first `SSR` with an identified player $s \\in \\{1, 2\\}$, count "
        "any `UNKNOWN PLAYER` `SSR` events before it ($u$), and infer the "
        "opening server as $(3 - s)$ if $u$ is even (or $s$ if $u$ is odd). "
        "Matches with no identifiable `SSR` or null `PT` actors are excluded.\n"
        "3. **Why Best of 5 and Best of 7 are pooled:** Unlike match win "
        "probabilities, a player's per-point win rate on serve or receive does "
        "not change theoretical scale between Bo5 and Bo7 matches."
    )

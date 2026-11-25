"""Win Rates Analytics Page.

Compares player Point %, Set %, and Match % win rates against exact theoretical
binomial models (with 11-point deuce rules) and an optional 2-parameter
logistic S-curve empirical fit, with Defenders visually highlighted against
Attackers and Best-of-5 strictly separated from Best-of-7 on match-level plots.
"""

from __future__ import annotations

from html import escape

import polars as pl
import streamlit as st
from lib.charts import render_chart
from lib.data import (
    data_files_exist,
    load_players,
    load_senior_games,
    load_senior_matches,
    load_snapshot_timestamp,
)
from lib.theme import ensure_standalone_shell
from lib.win_rates import (
    PlotMode,
    PlotSummaryMetrics,
    build_combined_point_set_stats,
    build_format_win_rates,
    build_win_rate_scatter,
    filter_win_rate_rows,
)

ensure_standalone_shell(load_snapshot_timestamp())


@st.cache_data(show_spinner=False)
def _cached_win_rate_tables(
    matches_height: int,
    games_height: int,
    players_height: int,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Build and cache combined (Point vs Set) and by-format (Bo5/Bo7) tables."""
    _ = (matches_height, games_height, players_height)
    matches = load_senior_matches()
    games = load_senior_games()
    players = load_players()
    return (
        build_combined_point_set_stats(matches, games, players),
        build_format_win_rates(matches, games, players),
    )


def _render_placeholder_box(tag: str, text: str) -> None:
    """Render an editable placeholder write-up card."""
    st.markdown(
        f"""
        <div class="tt-placeholder">
            <div class="tt-placeholder-tag">{escape(tag)}</div>
            {escape(text)}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_metrics_caption(metrics: PlotSummaryMetrics) -> None:
    """Render a compact Defender vs Attacker and model-fit summary caption."""
    if metrics.n_players == 0:
        return

    parts: list[str] = [
        f"**{metrics.n_players:,}** players "
        f"({metrics.n_attackers:,} Attackers, {metrics.n_defenders:,} Defenders)"
    ]
    if metrics.attacker_mean_delta is not None:
        parts.append(
            f"Attackers avg vs binomial: `{metrics.attacker_mean_delta:+.2f} pp`"
        )
    if metrics.defender_mean_delta is not None:
        parts.append(
            f"Defenders avg vs binomial: `{metrics.defender_mean_delta:+.2f} pp`"
        )
    if metrics.binomial_rmse is not None:
        parts.append(f"Binomial RMSE: `{metrics.binomial_rmse:.2f} pp`")
    if metrics.logistic_rmse is not None:
        parts.append(f"Logistic S-curve RMSE: `{metrics.logistic_rmse:.2f} pp`")

    st.caption(" · ".join(parts))


def _render_plot_section(
    *,
    section_key: str,
    subheader: str,
    placeholder_text: str,
    chart_title: str,
    mode: PlotMode,
    source_df: pl.DataFrame,
    has_format_toggle: bool,
    selected_styles: list[str],
    selected_grips: list[str],
    selected_hands: list[str],
    highlight_player: str | None,
) -> None:
    """Render one complete Win Rates plot section with per-plot controls."""
    st.subheader(subheader)
    _render_placeholder_box("Draft Write-Up (Placeholder)", placeholder_text)

    if has_format_toggle:
        c_gender, c_format = st.columns(2)
        with c_gender:
            gender_choice = (
                st.segmented_control(
                    "Gender",
                    options=["All", "Men", "Women"],
                    default="All",
                    key=f"{section_key}_gender",
                )
                or "All"
            )
        with c_format:
            format_choice = (
                st.segmented_control(
                    "Match Format",
                    options=["Best of 5", "Best of 7"],
                    default="Best of 5",
                    key=f"{section_key}_format",
                )
                or "Best of 5"
            )
        active_best_of: int | None = 5 if format_choice == "Best of 5" else 7
    else:
        gender_choice = (
            st.segmented_control(
                "Gender",
                options=["All", "Men", "Women"],
                default="All",
                key=f"{section_key}_gender",
            )
            or "All"
        )
        active_best_of = None

    c_slider, c_toggle = st.columns([3, 1.4], vertical_alignment="bottom")
    with c_slider:
        min_matches = st.slider(
            "Minimum played matches",
            min_value=0,
            max_value=100,
            value=10,
            step=5,
            key=f"{section_key}_min_matches",
            help=(
                "Excludes walkovers, injuries, retirements, and draws. "
                "Adjust independently for this plot."
            ),
        )
    with c_toggle:
        show_logistic = st.toggle(
            "2-param logistic S-curve",
            value=False,
            key=f"{section_key}_logistic",
            help=(
                "Overlay an empirical 2-parameter logistic S-curve fitted to "
                "the visible players alongside the theoretical binomial curve."
            ),
        )

    filtered = filter_win_rate_rows(
        source_df,
        gender=gender_choice,
        min_matches=min_matches,
        best_of=active_best_of,
        styles=selected_styles,
        grips=selected_grips,
        hands=selected_hands,
    )

    fig, metrics = build_win_rate_scatter(
        filtered,
        mode=mode,
        title=chart_title,
        best_of=active_best_of,
        gender_label=gender_choice,
        show_logistic_fit=show_logistic,
        highlight_player=highlight_player,
    )
    render_chart(fig, key=f"{section_key}_chart")
    _render_metrics_caption(metrics)


# ---------------------------------------------------------------------------
# Page Header & Top Summaries
# ---------------------------------------------------------------------------
st.title("📈 Win Rates")

_render_placeholder_box(
    "Introduction (Placeholder)",
    "Placeholder introduction: In table tennis, small edges in point win rate "
    "compound rapidly across 11-point sets and multi-set matches. Use the three "
    "interactive charts below to explore how players convert points into sets "
    "and matches across genders, Best-of-5 vs Best-of-7 formats, and playing "
    "styles (with Defenders highlighted in orange diamonds against Attackers). "
    "Only completed, played matches are included (walkovers, injuries, and "
    "retirements are excluded).",
)

st.markdown(
    """
    <div class="tt-summary-box">
        <strong>Reading the curves:</strong>
        The solid green <strong>Binomial curve</strong> shows the theoretical win
        rate if every point or set were an independent trial at a player's average
        probability &mdash; sitting <em>above</em> the curve means converting key
        moments more efficiently than raw averages predict, while sitting
        <em>below</em> means the reverse.<br><br>
        <strong>Optional 2-parameter Logistic S-curve:</strong>
        Each plot also includes a toggle for a dashed purple <strong>empirical
        logistic S-curve</strong> fitted directly to the visible players. Unlike a
        straight line (which can predict impossible win rates below 0% or above
        100%), a logistic S-curve stays strictly within 0%&ndash;100% and shows how
        real career averages tend to flatten relative to the theoretical binomial
        curve because players face a mix of weaker and stronger opponents.<br><br>
        <a href="#binomial-model-addendum">
            Read the full mathematical explanation for all three cases &darr;
        </a>
    </div>
    """,
    unsafe_allow_html=True,
)

if not data_files_exist():
    st.error(
        "Parquet snapshots were not found in `app/data/`. "
        "Run `make app-refresh` (or `uv run tt-app-export`) with PostgreSQL running, "
        "then refresh this page."
    )
    st.stop()

matches_df = load_senior_matches()
games_df = load_senior_games()
players_df = load_players()
combined_df, format_df = _cached_win_rate_tables(
    matches_df.height,
    games_df.height,
    players_df.height,
)

# ---------------------------------------------------------------------------
# Shared Secondary Profile Filters (Collapsed by default for clean mobile UX)
# ---------------------------------------------------------------------------
available_styles = sorted(combined_df["Style"].unique().to_list())
available_grips = sorted(combined_df["Grip"].unique().to_list())
available_hands = sorted(combined_df["Hand"].unique().to_list())
all_player_names = sorted(combined_df["Player"].unique().to_list())

with st.expander(
    "Player Profile Filters & Highlight (Style, Grip, Hand, Player Search)",
    expanded=False,
):
    pf1, pf2, pf3 = st.columns(3)
    with pf1:
        selected_styles = st.multiselect(
            "Playing Style",
            options=available_styles,
            default=available_styles,
            key="wr_styles",
        )
    with pf2:
        selected_grips = st.multiselect(
            "Grip",
            options=available_grips,
            default=available_grips,
            key="wr_grips",
        )
    with pf3:
        selected_hands = st.multiselect(
            "Playing Hand",
            options=available_hands,
            default=available_hands,
            key="wr_hands",
        )

    highlight_choice = st.selectbox(
        "Highlight a player across all three plots",
        options=["None", *all_player_names],
        index=0,
        key="wr_highlight_player",
    )
    highlight_player: str | None = (
        None if highlight_choice == "None" else highlight_choice
    )

st.divider()

# ---------------------------------------------------------------------------
# Plot 1: Point Win % vs Set Win % (Bo5 + Bo7 combined)
# ---------------------------------------------------------------------------
_render_plot_section(
    section_key="pt_vs_set",
    subheader="1. Point Win % vs Set Win %",
    placeholder_text=(
        "Placeholder write-up: Every senior singles set is played to 11 points "
        "(win by 2), regardless of whether the match is Best of 5 or Best of 7, "
        "so both formats are pooled here. Watch how tightly players hug the "
        "11-point deuce-aware binomial curve — and whether Defenders sit slightly "
        "above or below Attackers at the same point win rate."
    ),
    chart_title="Point Win Rate vs Set Win Rate",
    mode="point_to_set",
    source_df=combined_df,
    has_format_toggle=False,
    selected_styles=selected_styles,
    selected_grips=selected_grips,
    selected_hands=selected_hands,
    highlight_player=highlight_player,
)

st.divider()

# ---------------------------------------------------------------------------
# Plot 2: Set Win % vs Match Win % (Best of 5 vs Best of 7 strictly separate)
# ---------------------------------------------------------------------------
_render_plot_section(
    section_key="set_vs_match",
    subheader="2. Set Win % vs Match Win %",
    placeholder_text=(
        "Placeholder write-up: Switching between Best of 5 and Best of 7 shows "
        "how match length amplifies skill differences. A Best-of-7 curve is "
        "steeper around 50%, rewarding players who consistently win more than "
        "half of their sets. Best of 5 and Best of 7 are kept strictly separate "
        "so each chart matches its exact theoretical curve."
    ),
    chart_title="Set Win Rate vs Match Win Rate",
    mode="set_to_match",
    source_df=format_df,
    has_format_toggle=True,
    selected_styles=selected_styles,
    selected_grips=selected_grips,
    selected_hands=selected_hands,
    highlight_player=highlight_player,
)

st.divider()

# ---------------------------------------------------------------------------
# Plot 3: Point Win % vs Match Win % (Best of 5 vs Best of 7 strictly separate)
# ---------------------------------------------------------------------------
_render_plot_section(
    section_key="pt_vs_match",
    subheader="3. Point Win % vs Match Win %",
    placeholder_text=(
        "Placeholder write-up: Combining both stages — points into 11-point "
        "sets, and sets into a Best-of-5 or Best-of-7 match — reveals the full "
        "leverage of table tennis scoring. Winning just 55% of points corresponds "
        "to an overwhelmingly high theoretical match win rate, while the "
        "empirical logistic S-curve shows how much real career schedules flatten "
        "that slope."
    ),
    chart_title="Point Win Rate vs Match Win Rate",
    mode="point_to_match",
    source_df=format_df,
    has_format_toggle=True,
    selected_styles=selected_styles,
    selected_grips=selected_grips,
    selected_hands=selected_hands,
    highlight_player=highlight_player,
)

st.divider()

# ---------------------------------------------------------------------------
# Mathematical Addendum (Anchor target from top summary link)
# ---------------------------------------------------------------------------
st.markdown('<div id="binomial-model-addendum"></div>', unsafe_allow_html=True)
st.subheader("Mathematical Addendum: Binomial & Logistic S-Curve Models")

with st.expander(
    "Read the full mathematical explanation for all three cases",
    expanded=False,
):
    st.markdown(
        "Each theoretical green curve assumes independent, identically "
        "distributed (i.i.d.) trials with constant win probability. Below is the "
        "exact derivation for each of the three plots, plus the optional "
        "2-parameter logistic S-curve."
    )

    st.markdown("#### Case 1: Point Win % → Set Win % (11-Point Set with Deuce)")
    st.markdown(
        "Let $p$ be the probability of winning a single point and $q = 1 - p$. "
        "A standard table tennis set is first to **11 points, win by 2**, so "
        "there are two mutually exclusive ways to win:"
    )
    st.markdown(
        "1. **Straight win ($11\\text{-}0$ through $11\\text{-}9$):** You win "
        "the final (11th) point after your opponent wins $j \\in \\{0, \\dots, 9\\}$ "
        "points among the first $10 + j$ points.\n"
        "2. **Deuce ($10\\text{-}10$) and win by 2:** Both players win 10 of the "
        "first 20 points, and from deuce the win-by-2 race is a geometric series "
        "with win probability $\\frac{p^2}{p^2 + q^2}$."
    )
    st.latex(
        r"\begin{aligned}"
        r"P(\text{Set} \mid p) &= "
        r"\sum_{j=0}^{9} \binom{10 + j}{j} p^{11}(1 - p)^{j} \\"
        r"&\quad + \binom{20}{10} p^{10}(1 - p)^{10} \cdot \frac{p^2}{p^2 + (1 - p)^2}"
        r"\end{aligned}"
    )

    st.markdown("#### Case 2: Set Win % → Match Win % (Best of 5 vs Best of 7)")
    st.markdown(
        "Let $s$ be the probability of winning a single set. In a Best-of-$N$ "
        "match ($N \\in \\{5, 7\\}$), a player needs $k = (N + 1) / 2$ sets "
        "(3 for Best of 5; 4 for Best of 7). Playing out the equivalent of $N$ "
        "independent sets gives the binomial tail probability:"
    )
    st.latex(
        r"\begin{aligned}"
        r"P(\text{Match} \mid s, N) &= "
        r"\sum_{i=k}^{N} \binom{N}{i} s^{i}(1 - s)^{N - i}, \\"
        r"k &= \frac{N + 1}{2}"
        r"\end{aligned}"
    )
    st.markdown(
        "Because $N = 7$ aggregates more sets than $N = 5$, its S-curve is "
        "steeper around $s = 50\\%$: a stronger player ($s > 50\\%$) has a "
        "higher win probability in Best of 7 than in Best of 5."
    )

    st.markdown("#### Case 3: Point Win % → Match Win % (Two-Stage Composition)")
    st.markdown(
        "Combining Case 1 and Case 2 directly maps per-point win probability "
        "$p$ to match win probability:"
    )
    st.latex(
        r"\begin{aligned}"
        r"P(\text{Match} \mid p, N) &= P(\text{Match} \mid s,\, N), \\"
        r"\text{where } s &= P(\text{Set} \mid p)"
        r"\end{aligned}"
    )

    st.markdown("#### The 2-Parameter Logistic S-Curve & Why Real Data Deviates")
    st.markdown(
        "When toggled on, the dashed purple curve fits a smooth 2-parameter "
        "logistic function directly to the filtered players:"
    )
    st.latex(
        r"\hat{y}(x) = \frac{100}{1 + \exp\!\left(-\left(a + b \cdot "
        r"\frac{x - 50}{100}\right)\right)}"
    )
    st.markdown(
        "Unlike a straight line (which can extrapolate below 0% or above 100%), "
        "the logistic curve stays strictly bounded in $(0\\%, 100\\%)$. In real "
        "career data, the empirical S-curve is noticeably **flatter** than the "
        "theoretical binomial curve for several reasons:\n"
        "- **Opponent heterogeneity (Jensen's inequality):** Career averages "
        "pool blowouts against weaker opponents and losses against stronger "
        "opponents; averaging across unequal matches compresses extreme binomial "
        "predictions toward the middle.\n"
        "- **Non-independent points & sets:** Serve/receive alternation, "
        "tactical adaptations between games, momentum, and fatigue violate the "
        "strict i.i.d. assumption.\n"
        "- **Clutch play & playing styles:** Players who save their best "
        "serves or variations for deuce (or Defenders whose long rallies shift "
        "variance) can systematically sit above or below the baseline."
    )

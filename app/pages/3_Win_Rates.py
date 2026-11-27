"""Win Rates Analytics Page.

Compares player Match %, Set %, and Point % win rates against exact theoretical
binomial models (with 11-point deuce rules) and an optional 2-parameter
logistic S-curve empirical fit, with Defenders visually highlighted against
Attackers and Best-of-5 strictly separated from Best-of-7 on match-level plots.
"""

from __future__ import annotations

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
    MatchPointShareMetrics,
    PlotMode,
    PlotSummaryMetrics,
    build_combined_point_set_stats,
    build_delta_extremes,
    build_format_win_rates,
    build_match_point_share_bins,
    build_match_point_share_chart,
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


@st.cache_data(show_spinner=False)
def _cached_match_point_share_bins(
    matches_height: int,
    best_of: int,
    gender: str,
) -> tuple[pl.DataFrame, int, int, int]:
    """Build and cache 1% centered match point-share bins for a format/gender."""
    _ = matches_height
    return build_match_point_share_bins(
        load_senior_matches(),
        best_of=best_of,
        gender=gender,
    )


def _render_insight_card(bullets_html: list[str]) -> None:
    """Render a clean editorial insight card with structured bullet points."""
    items = "".join(f"<li>{item}</li>" for item in bullets_html)
    st.markdown(
        f'<div class="tt-insight-card"><ul>{items}</ul></div>',
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


def _render_extremes_expander(
    filtered: pl.DataFrame,
    *,
    mode: PlotMode,
    best_of: int | None,
) -> None:
    """Render a mobile-friendly expander showing top over- and under-performers."""
    if filtered.height < 2:
        return
    above, below = build_delta_extremes(filtered, mode=mode, best_of=best_of, top_n=5)
    with st.expander("Inspect players furthest above & below the binomial curve"):
        tab_hi, tab_lo = st.tabs(["Furthest above binomial", "Furthest below binomial"])
        with tab_hi:
            st.caption("Players outperforming their raw rate vs the binomial baseline.")
            st.dataframe(above.to_pandas(), hide_index=True, width="stretch")
        with tab_lo:
            st.caption("Players trailing the binomial baseline given their raw rate.")
            st.dataframe(below.to_pandas(), hide_index=True, width="stretch")


def _render_plot_section(
    *,
    section_key: str,
    anchor_id: str,
    subheader: str,
    bullets_html: list[str],
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
    st.markdown(f'<div id="{anchor_id}"></div>', unsafe_allow_html=True)
    st.subheader(subheader)
    _render_insight_card(bullets_html)

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

    c_slider, c_toggle = st.columns([2.1, 1.7], vertical_alignment="bottom")
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
            "Show fitted S-curve",
            value=False,
            key=f"{section_key}_logistic",
            help=(
                "Overlay a smooth S-curve fitted to the visible players "
                "alongside the theoretical binomial curve."
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
    _render_extremes_expander(filtered, mode=mode, best_of=active_best_of)


def _render_match_share_caption(metrics: MatchPointShareMetrics) -> None:
    """Render a compact summary caption beneath the per-match histogram."""
    if metrics.n_matches == 0:
        return

    parts: list[str] = [f"**{metrics.n_matches:,}** played matches"]
    if metrics.won_below_50_pct is not None:
        parts.append(
            f"Won with <50% points: **{metrics.won_below_50:,}** "
            f"(`{metrics.won_below_50_pct:.2f}%`)"
        )
    parts.append(f"Exact 50% point ties won: **{metrics.won_at_50:,}**")
    if metrics.binomial_rmse is not None:
        parts.append(f"Weighted Binomial RMSE: `{metrics.binomial_rmse:.2f} pp`")
    if metrics.logistic_rmse is not None:
        parts.append(f"Logistic S-curve RMSE: `{metrics.logistic_rmse:.2f} pp`")
    st.caption(" · ".join(parts))


# ---------------------------------------------------------------------------
# Page Header & Executive Summary
# ---------------------------------------------------------------------------
st.title("Win Rates")
st.markdown(
    "Winning just a few extra points per game adds up fast across 11-point "
    "sets and multi-set matches. Below, we compare what pure probability "
    "predicts against how pro players actually perform across four views:\n"
    "- **1. Match % vs Point % (per player):** How career point win rate "
    "turns into match wins.\n"
    "- **2. Match % vs Set % (per player):** How set win rate turns into "
    "Best-of-5 and Best-of-7 match wins.\n"
    "- **3. Set % vs Point % (per player):** How closely single 11-point sets "
    "follow the math.\n"
    "- **4. Match Win % vs Point Share (per match):** How often winning a "
    "given share of points inside a single match wins that match.\n\n"
    "*Includes completed senior singles matches only (walkovers, injuries, "
    "retirements, and draws are excluded).*"
)

st.markdown(
    """
    <div class="tt-summary-box">
        <strong>How to read these charts:</strong>
        <ul>
            <li>
                <strong>Green curve (theory):</strong> Expected win rate if
                every point or set were an independent coin flip at a player's
                average rate. Players <em>above</em> the line win more than
                expected; players <em>below</em> win less.
            </li>
            <li>
                <strong>Dashed purple curve (fitted S-curve toggle):</strong>
                Smooth curve fitted to the real data, showing where pro results
                flatten (Plots 1-3) or steepen (Plot 4) compared to theory.
            </li>
            <li>
                <strong>Defenders vs Attackers (Plots 1-3):</strong>
                <strong>Defenders</strong> appear as larger
                <strong>orange diamonds</strong> over <strong>blue Attacker
                circles</strong>, with dotted lines at 50%.
            </li>
            <li>
                <strong>Per-match point share (Plot 4):</strong> Groups every
                match's winner and loser into 1% point-share bins across the
                full dataset.
            </li>
        </ul>
        <a href="#binomial-model-addendum">
            Jump to Methodology &amp; Math &darr;
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

st.markdown(
    """
    <div class="tt-jump-bar">
        <a href="#plot-1-point-vs-match">1. Match % vs Point %</a>
        <a href="#plot-2-set-vs-match">2. Match % vs Set %</a>
        <a href="#plot-3-point-vs-set">3. Set % vs Point %</a>
        <a href="#plot-4-match-point-share">4. Per-Match Point Share</a>
        <a href="#binomial-model-addendum">Methodology &amp; Math</a>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Shared Player Highlight (Surfaced) & Secondary Profile Filters (Collapsed)
# ---------------------------------------------------------------------------
available_styles = sorted(combined_df["Style"].unique().to_list())
available_grips = sorted(combined_df["Grip"].unique().to_list())
available_hands = sorted(combined_df["Hand"].unique().to_list())
all_player_names = sorted(combined_df["Player"].unique().to_list())

highlight_choice = st.selectbox(
    "Highlight a player across Plots 1-3",
    options=["None", *all_player_names],
    index=0,
    key="wr_highlight_player",
    help="Spot any player as a red star across the three player-level scatter plots.",
)
highlight_player: str | None = None if highlight_choice == "None" else highlight_choice

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

st.divider()

# ---------------------------------------------------------------------------
# Plot 1: Match Win Rate vs Point Win Rate (Bo5 vs Bo7 strictly separate)
# ---------------------------------------------------------------------------
_render_plot_section(
    section_key="pt_vs_match",
    anchor_id="plot-1-point-vs-match",
    subheader="1. Match Win Rate vs Point Win Rate",
    bullets_html=[
        (
            "<strong>Theory vs. reality:</strong> Winning <strong>52%</strong> "
            "of points predicts <strong>64.2%</strong> match wins in Best of 5 "
            "(<strong>66.5%</strong> in Best of 7), and <strong>55%</strong> "
            "predicts <strong>81.9%</strong> in Bo5 (<strong>85.6%</strong> in "
            "Bo7). In practice, players follow a flatter curve: at <strong>55%"
            "</strong> points, the fitted curve lands around <strong>72%-73%"
            "</strong> instead of 82%-86% (~7 pp below theory in Bo5, ~11 pp in "
            "Bo7), while players below <strong>48%</strong> sit <strong>6-12 pp "
            "above</strong> theory."
        ),
        (
            "<strong>Why real careers flatten:</strong> Lopsided <code>11-4"
            "</code> wins over weaker opponents lift career Point % without "
            "adding extra match wins, while losses against peers happen in "
            "tighter matches."
        ),
        (
            "<strong>Defenders trail the curve:</strong> Orange <strong>Defender"
            "</strong> diamonds average <code>&minus;3.0 pp</code> vs. theory "
            "in Bo5 and <code>&minus;7.5 pp</code> in Bo7. Defenders often beat "
            "opponents who struggle against chop by wide point margins, then "
            "lose tight tactical matches to top attackers who adjust over "
            "longer Bo7 matches."
        ),
    ],
    chart_title="Match Win Rate vs Point Win Rate",
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
# Plot 2: Match Win Rate vs Set Win Rate (Bo5 vs Bo7 strictly separate)
# ---------------------------------------------------------------------------
_render_plot_section(
    section_key="set_vs_match",
    anchor_id="plot-2-set-vs-match",
    subheader="2. Match Win Rate vs Set Win Rate",
    bullets_html=[
        (
            "<strong>Closer to theory:</strong> Winning <strong>60%</strong> of "
            "sets predicts <strong>68.3%</strong> of Bo5 matches and "
            "<strong>71.0%</strong> of Bo7 matches. Counting sets ignores blowout "
            "set scores (<code>11-2</code> counts the same as <code>11-9</code>"
            "), dropping Binomial RMSE from <code>7.4</code> to <code>4.8 pp"
            "</code> in Bo5 and from <code>10.5</code> to <code>7.4 pp</code> "
            "in Bo7."
        ),
        (
            "<strong>Remaining gap:</strong> The fitted curve is still slightly "
            "flatter (~65% at 60% sets) because players mix <code>3-0</code> "
            "sweeps with <code>2-3</code> losses and adapt mid-match, while "
            "Defenders still sit below the line (<code>&minus;1.5 pp</code> in "
            "Bo5, <code>&minus;5.1 pp</code> in Bo7)."
        ),
        (
            "<strong>Separate formats:</strong> Best of 5 and Best of 7 are "
            "plotted separately so each uses its exact theoretical curve."
        ),
    ],
    chart_title="Match Win Rate vs Set Win Rate",
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
# Plot 3: Set Win Rate vs Point Win Rate (Bo5 + Bo7 combined)
# ---------------------------------------------------------------------------
_render_plot_section(
    section_key="pt_vs_set",
    anchor_id="plot-3-point-vs-set",
    subheader="3. Set Win Rate vs Point Win Rate",
    bullets_html=[
        (
            "<strong>Closest match to theory:</strong> Every set is first to 11 "
            "(win by 2) in both Bo5 and Bo7, so both formats pool cleanly. "
            "Winning <strong>52%</strong> of points predicts <strong>57.7%"
            "</strong> of sets, and <strong>55%</strong> predicts <strong>68.7%"
            "</strong>."
        ),
        (
            "<strong>Attackers and Defenders align:</strong> Across 1,181 players "
            "(<code>3.09 pp</code> Binomial RMSE; <strong>66.1%</strong> fitted "
            "vs. <strong>68.7%</strong> predicted at 55% points), Attackers "
            "(<code>&minus;0.02 pp</code>) and Defenders (<code>&minus;0.62 pp"
            "</code>) sit right on the line. That confirms the Defender gap in "
            "Plots 1 &amp; 2 emerges <em>between</em> sets rather than "
            "<em>within</em> them."
        ),
        (
            "<strong>Small remaining spread:</strong> Comes from clutch deuce "
            "play at <code>9-9</code> and <code>10-10</code>, easing off in "
            "trailing sets (<code>2-8</code>), and serve/receive swings."
        ),
    ],
    chart_title="Set Win Rate vs Point Win Rate",
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
# Plot 4: Match Win Probability vs Match Point Share (Per-Match 1% Histogram)
# ---------------------------------------------------------------------------
st.markdown('<div id="plot-4-match-point-share"></div>', unsafe_allow_html=True)
st.subheader("4. Match Point Share vs Match Win Probability (Per-Match)")
_render_insight_card(
    [
        (
            "<strong>Winning with fewer points (~3.7% of matches):</strong> "
            "Because matches go by sets (first to 3 or 4) rather than total "
            "points, <strong>1,094</strong> of 29,396 Bo5 winners "
            "(<code>3.72%</code>; Men <code>3.80%</code>, Women "
            "<code>3.63%</code>) and <strong>143</strong> of 3,938 Bo7 winners "
            "(<code>3.63%</code>; Men <code>3.93%</code>, Women "
            "<code>3.30%</code>) won despite scoring &lt;50% of points."
        ),
        (
            "<strong>Steeper than career averages:</strong> Across 1% "
            "point-share bins (counting both sides of every match), win rate "
            "climbs from near <code>0%</code> to <code>100%</code> between "
            "<code>44%</code> and <code>56%</code>. Winning <strong>52%</strong> "
            "of points inside a match wins <strong>85.1%</strong> in Bo5 and "
            "<strong>89.0%</strong> in Bo7 (vs. <code>64.2%</code> / "
            "<code>66.5%</code> on the career curve), and the fitted S-curve "
            "matches the bins within <code>~0.45 pp</code> weighted RMSE."
        ),
        (
            "<strong>Why it's steeper:</strong> Plots 1-3 look at a player's "
            "long-run average across many opponents. Here, all points inside "
            "that match are already scored, so only how points cluster across "
            "sets (like losing two <code>3-11</code> sets while winning three "
            "<code>11-9</code> sets) lets a player win despite scoring fewer "
            "total points."
        ),
    ]
)

m_col1, m_col2 = st.columns(2)
with m_col1:
    match_gender = (
        st.segmented_control(
            "Gender",
            options=["All", "Men", "Women"],
            default="All",
            key="match_pt_share_gender",
        )
        or "All"
    )
with m_col2:
    match_format = (
        st.segmented_control(
            "Match Format",
            options=["Best of 5", "Best of 7"],
            default="Best of 5",
            key="match_pt_share_format",
        )
        or "Best of 5"
    )
match_best_of = 5 if match_format == "Best of 5" else 7

show_match_logistic = st.toggle(
    "Show fitted S-curve",
    value=True,
    key="match_pt_share_logistic",
    help=(
        "Overlay the smooth S-curve fitted to the 1% point-share bins "
        "alongside the theoretical binomial curve."
    ),
)

bins_df, n_matches, won_below_50, won_at_50 = _cached_match_point_share_bins(
    matches_df.height,
    match_best_of,
    match_gender,
)
fig_match, match_metrics = build_match_point_share_chart(
    bins_df,
    best_of=match_best_of,
    n_matches=n_matches,
    won_below_50=won_below_50,
    won_at_50=won_at_50,
    gender_label=match_gender,
    show_logistic_fit=show_match_logistic,
)
render_chart(fig_match, key="match_pt_share_chart")
_render_match_share_caption(match_metrics)

if not bins_df.is_empty():
    transition_bins = bins_df.filter(pl.col("Point % Bin").is_between(43, 57))
    display_bins = transition_bins if not transition_bins.is_empty() else bins_df
    with st.expander("Inspect 1% match point-share bins (transition zone)"):
        st.caption(
            "Centered 1% bins ([c - 0.5%, c + 0.5%)) counting both winner and "
            "loser sides per match."
        )
        st.dataframe(display_bins.to_pandas(), hide_index=True, width="stretch")

st.divider()

# ---------------------------------------------------------------------------
# Mathematical Addendum (Anchor target from top summary link)
# ---------------------------------------------------------------------------
st.markdown('<div id="binomial-model-addendum"></div>', unsafe_allow_html=True)
st.subheader("Methodology & Math")

with st.expander(
    "Binomial formulas, logistic S-curve fit, and why pro data flattens",
    expanded=False,
):
    st.markdown(
        "Each theoretical green curve assumes independent, identically "
        "distributed (i.i.d.) trials with constant win probability. Below is "
        "how the two building blocks (Plot 3 and Plot 2) combine to produce "
        "Plot 1, followed by the optional 2-parameter logistic S-curve."
    )

    st.markdown("#### Plot 3: Point Win % → Set Win % (11-Point Set with Deuce)")
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
        r"\sum_{j=0}^{9} \binom{10 + j}{j} p^{11} q^{j} \\"
        r"&\quad + \binom{20}{10} p^{10} q^{10} \cdot \frac{p^2}{p^2 + q^2}"
        r"\end{aligned}"
    )

    st.markdown("#### Plot 2: Set Win % → Match Win % (Best of 5 vs Best of 7)")
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

    st.markdown("#### Plot 1: Point Win % → Match Win % (Two-Stage Composition)")
    st.markdown(
        "Composing Plot 3 into Plot 2 directly maps per-point win probability "
        "$p$ to match win probability:"
    )
    st.latex(
        r"\begin{aligned}"
        r"P(\text{Match} \mid p, N) &= P(\text{Match} \mid s,\, N), \\"
        r"\text{where } s &= P(\text{Set} \mid p)"
        r"\end{aligned}"
    )

    st.markdown("#### The 2-Parameter Logistic S-Curve & Why Real Data Flattens")
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
        "theoretical binomial curve for three main reasons:\n"
        "- **Opponent heterogeneity (Jensen's inequality):** Career averages "
        "pool blowouts against weaker opponents and close losses against peers; "
        "averaging across unequal matches compresses extreme binomial "
        "predictions toward the middle.\n"
        "- **Non-independent points & sets:** Serve/receive alternation, "
        "tactical adaptations between games, momentum, and fatigue violate the "
        "strict i.i.d. assumption.\n"
        "- **Clutch play & playing styles:** Players who save their best "
        "variations for deuce, or Defenders whose match-to-match variance is "
        "higher across different opponents, systematically separate from the "
        "baseline."
    )

    st.markdown("#### Plot 4: Per-Match Realized Point Share (1% Centered Bins)")
    st.markdown(
        "Each played match contributes both sides: $(x, 1)$ for the winner and "
        "$(100 - x, 0)$ for the loser, where $x$ is that side's percentage of "
        "total points in the match. Binning to nearest integer percentages "
        "($[c - 0.5\\%,\\, c + 0.5\\%)$) centers exact $50\\%$ ties at $50\\%$ "
        "and preserves mirror symmetry $P(\\text{Win} \\mid x) + "
        "P(\\text{Win} \\mid 100 - x) = 100\\%$. Conditioning on realized "
        "$\\hat{p}_{\\text{match}}$ produces a far steeper transition than "
        "$P(\\text{Match} \\mid p_{\\text{true}})$ because the total-point "
        "outcome is already fixed, so only set-level point clustering can alter "
        "the match winner."
    )

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
    PlotMode,
    PlotSummaryMetrics,
    build_combined_point_set_stats,
    build_delta_extremes,
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
    _render_extremes_expander(filtered, mode=mode, best_of=active_best_of)


# ---------------------------------------------------------------------------
# Page Header & Executive Summary
# ---------------------------------------------------------------------------
st.title("📈 Win Rates")
st.markdown(
    "In table tennis, small edges at the point level compound across 11-point "
    "sets and multi-set matches — **at least in theory**. Below we break that "
    "relationship into three steps: starting with the headline **Match % vs "
    "Point %** gap, then decomposing it into **Match % vs Set %** and "
    "**Set % vs Point %** to pinpoint *where* real players diverge from pure "
    "probability. Only completed, played senior singles matches are included "
    "(walkovers, injuries, retirements, and draws are excluded)."
)

st.markdown(
    """
    <div class="tt-summary-box">
        <strong>How to read these charts:</strong>
        <ul>
            <li>
                <strong>Green Binomial curve (theory):</strong> The exact
                expected win rate if every point or set were an independent trial
                at a player's career average. Dots <em>above</em> the curve win
                more efficiently than their raw rate predicts; dots <em>below</em>
                leave wins on the table.
            </li>
            <li>
                <strong>Dashed purple 2-parameter Logistic S-curve (toggle):</strong>
                An empirical S-curve fitted directly to the players on screen
                (strictly bounded in 0%&ndash;100%). Comparing it to the green
                curve shows how much real career schedules flatten the theoretical
                slope.
            </li>
            <li>
                <strong>Defenders vs Attackers:</strong> <strong>Defenders</strong>
                are highlighted in larger <strong>orange diamonds</strong> on top
                of <strong>blue Attacker circles</strong>, anchored around the
                dotted 50% break-even crosshairs.
            </li>
        </ul>
        <a href="#binomial-model-addendum">
            Jump to the full mathematical explanation (Addendum) &darr;
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
# Plot 1: Match Win Rate vs Point Win Rate (Bo5 vs Bo7 strictly separate)
# ---------------------------------------------------------------------------
_render_plot_section(
    section_key="pt_vs_match",
    subheader="1. Match Win Rate vs Point Win Rate",
    bullets_html=[
        (
            "<strong>On paper:</strong> Combining 11-point sets with multi-set "
            "matches creates huge leverage. Winning <strong>55% of points</strong> "
            "predicts an <strong>81.9%</strong> match win rate in Best of 5 and "
            "<strong>85.6%</strong> in Best of 7 (even 52% points predicts "
            "64.2% in Bo5 and 66.5% in Bo7)."
        ),
        (
            "<strong>What the data shows:</strong> Real players consistently "
            "sit <em>flatter</em> than the green curve — in the upper-right "
            "quadrant (&gt;52% points), players trail theory by ~7 pp in Bo5 and "
            "~11 pp in Bo7 (at 55% points the empirical S-curve lands around "
            "<strong>72%&ndash;73%</strong>, not 82%&ndash;86%), while players "
            "below 48% points sit 6&ndash;12 pp <em>above</em> it. Toggle the "
            "<strong>2-param logistic S-curve</strong> to see this gap directly. "
            "Orange <strong>Defender</strong> diamonds also sit below the curve "
            "on average (&minus;3.0 pp in Bo5, &minus;7.5 pp in Bo7)."
        ),
        (
            "<strong>Why:</strong> Career averages pool mismatched opponents "
            "together. Running up 11&ndash;4 sets against weaker early-round "
            "opponents inflates a player's Point % without granting extra match "
            "wins, while tough losses to peers happen in close matches "
            "(<strong>opponent heterogeneity / Jensen's inequality</strong>). "
            "For Defenders, lopsided wins over players who struggle against chop "
            "paired with tight tactical losses to top attackers who adapt over a "
            "longer match magnify that gap — especially in Best of 7."
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
    subheader="2. Match Win Rate vs Set Win Rate",
    bullets_html=[
        (
            "<strong>On paper:</strong> With sets already decided, fewer "
            "independent trials remain so the green curve is gentler: winning "
            "<strong>60% of sets</strong> translates to <strong>68.3%</strong> "
            "of Best-of-5 matches and <strong>71.0%</strong> of Best-of-7 "
            "matches. Best of 5 and Best of 7 stay strictly separate so each "
            "format is measured against its own exact curve."
        ),
        (
            "<strong>What the data shows:</strong> The scatter tightens "
            "substantially compared to Plot 1 (Binomial RMSE drops from "
            "<code>7.4</code> to <code>4.8 pp</code> in Bo5, and from "
            "<code>10.5</code> to <code>7.4 pp</code> in Bo7). Even so, the "
            "empirical logistic S-curve stays noticeably flatter than theory "
            "(~65% vs 68%&ndash;71% at 60% sets), and Defenders still sit below "
            "the line (&minus;1.5 pp in Bo5, &minus;5.1 pp in Bo7)."
        ),
        (
            "<strong>Why:</strong> Moving from points to sets strips out "
            "within-set blowouts (winning 11&ndash;2 counts the same as "
            "11&ndash;9), removing much of the noise from Plot 1. The remaining "
            "flattening comes from <strong>match-to-match schedule mix</strong> "
            "(3&ndash;0 wins vs 2&ndash;3 losses) and <strong>in-match "
            "adaptation</strong> — sets within a match aren't truly independent "
            "as players adjust serve/receive tactics and read spin better over "
            "longer Bo7 battles."
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
    subheader="3. Set Win Rate vs Point Win Rate",
    bullets_html=[
        (
            "<strong>On paper:</strong> Because every senior singles set is "
            "first to 11 (win by 2) regardless of match length, Bo5 and Bo7 "
            "matches are pooled onto a single deuce-aware binomial curve. "
            "Winning <strong>52% of points</strong> predicts <strong>57.7%"
            "</strong> of sets, and <strong>55% of points</strong> predicts "
            "<strong>68.7%</strong> of sets."
        ),
        (
            "<strong>What the data shows:</strong> This is by far the tightest "
            "fit to theory on the page (<code>3.09 pp</code> Binomial RMSE "
            "across 1,181 players; at 55% points the empirical S-curve sits at "
            "<strong>66.1%</strong> vs <strong>68.7%</strong> theoretical). "
            "Attackers average dead-on the curve (<code>&minus;0.02 pp</code>) "
            "and Defenders are nearly neutral (<code>&minus;0.62 pp</code>) — "
            "showing that almost all of the Defender gap in Plots 1 &amp; 2 "
            "emerges <em>between</em> sets rather than <em>within</em> them."
        ),
        (
            "<strong>Why:</strong> Inside a single 11-point set, point-by-point "
            "variance dominates and opponent-level mismatch has far less room "
            "to distort the curve than across an entire match. The small "
            "residual spread reflects <strong>clutch deuce play</strong> "
            "(holding serve or saving key variations at 9&ndash;9 / 10&ndash;10), "
            "occasional eased-off sets when trailing 2&ndash;8, and subtle "
            "serve-receive dynamics."
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
# Mathematical Addendum (Anchor target from top summary link)
# ---------------------------------------------------------------------------
st.markdown('<div id="binomial-model-addendum"></div>', unsafe_allow_html=True)
st.subheader("Mathematical Addendum: Binomial & Logistic S-Curve Models")

with st.expander(
    "Read the full mathematical explanation for all three plots",
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
        "variations for deuce — or Defenders whose match-to-match variance is "
        "higher across different opponents — systematically separate from the "
        "baseline."
    )

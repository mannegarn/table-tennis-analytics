"""Table Types: Plain-English Surface Win Rates, Upsets & China H0 Tests.

Separates Men's Singles and Women's Singles with a prominent top toggle,
removes technical jargon from main charts/takeaways, and tucks deeper
statistical breakdowns neatly below each visualization.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd
import polars as pl
import streamlit as st
from lib.charts import GENDER_COLORS, render_chart
from lib.data import (
    data_files_exist,
    load_events,
    load_glicko_match_ratings,
    load_players,
    load_snapshot_timestamp,
    load_table_brands,
)
from lib.table_types import (
    SURFACE_GRIPPY,
    SURFACE_SKIDDY,
    TIER_OPTIONS,
    PairedContrastSummary,
    SurfaceKPISummary,
    TiltAnalysisResult,
    build_brand_summary_matrix,
    build_cohort_residuals,
    build_player_surface_boosts,
    build_player_tilt_analysis,
    build_residual_forest_plot,
    build_tilt_comparison_charts,
    build_upset_residual_chart,
    compute_kpi_summary,
    compute_paired_chn_contrast,
    prepare_surface_matches,
    summarize_brand_groups,
    unpivot_surface_player_matches,
)
from lib.theme import ensure_standalone_shell
from lib.upsets import attach_prior_match_counts

ensure_standalone_shell(load_snapshot_timestamp())

POOL_LABELS: dict[str, Literal["men", "women"]] = {
    "Men's Singles": "men",
    "Women's Singles": "women",
}

GROUPING_LABELS: dict[str, Literal["surface", "brand"]] = {
    "Asian vs. European Tables": "surface",
    "By Table Brand": "brand",
}


@st.cache_data(show_spinner=False)
def _cached_ratings_with_prior(ratings_height: int) -> pl.DataFrame:
    _ = ratings_height
    return attach_prior_match_counts(load_glicko_match_ratings())


@st.cache_data(show_spinner=False)
def _cached_surface_frames(
    ratings_height: int,
    events_height: int,
    players_height: int,
    brands_height: int,
    gender: Literal["men", "women"],
    tiers: tuple[str, ...],
    min_prior_matches: int,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    _ = (ratings_height, events_height, players_height, brands_height)
    players = load_players()
    surface_matches = prepare_surface_matches(
        _cached_ratings_with_prior(ratings_height),
        load_events(),
        players,
        load_table_brands(),
        gender=gender,
        tiers=tiers if tiers else None,
        start_date=None,
        end_date=None,
        min_prior_matches=min_prior_matches,
    )
    player_matches = unpivot_surface_player_matches(surface_matches, players)
    return surface_matches, player_matches


@st.cache_data(show_spinner=False)
def _cached_brand_groups(brands_height: int) -> dict[str, list[str]]:
    """Summarize and cache sorted table brand lists per table group."""
    _ = brands_height
    return summarize_brand_groups(load_table_brands())


def _format_pct(val: float | None, *, default: str = "\u2014") -> str:
    if val is None:
        return default
    return f"{val:+.1f}%"


def _format_p_val(p: float | None) -> str:
    if p is None:
        return "n/a"
    if p < 0.001:
        return "< 0.001"
    return f"{p:.3f}"


def _diverging_residual_css(val: object, *, scale: float = 8.0) -> str:
    """Return subtle terracotta (+) vs steel-blue (-) background tint CSS."""
    if not isinstance(val, (int, float)) or pd.isna(val):
        return ""
    v = float(val)
    if abs(v) < 0.05:
        return ""
    alpha = min(0.32, 0.05 + 0.27 * min(1.0, abs(v) / scale))
    if v > 0:
        return f"background-color: rgba(194, 109, 69, {alpha:.3f});"
    return f"background-color: rgba(74, 124, 155, {alpha:.3f});"


def _render_insight_bullets(
    items_html: list[str],
    *,
    box_class: str = "tt-insight-card",
) -> None:
    """Render a compact card with plain-English bullet points."""
    lis = "".join(f"<li>{item}</li>" for item in items_html)
    st.markdown(
        f'<div class="{box_class}"><ul>{lis}</ul></div>',
        unsafe_allow_html=True,
    )


def _render_top_takeaways(
    kpis: SurfaceKPISummary,
    paired: PairedContrastSummary,
    *,
    pool_label: str,
    chn_noun: str,
) -> None:
    """Render concise summary takeaways directly beneath the KPI strip."""
    bullets: list[str] = []

    if (
        kpis.chn_h2h_asian_resid_pp is not None
        and kpis.chn_h2h_euro_resid_pp is not None
    ):
        paired_clause = ""
        if paired.n_players > 0 and paired.p_value is not None:
            clarity = (
                "statistically consistent"
                if paired.p_value < 0.05
                else "not statistically significant"
            )
            paired_clause = (
                f" Among the <strong>{paired.n_players}</strong> {chn_noun} who "
                f"played on both surfaces, <strong>{paired.n_better_on_asian} of "
                f"{paired.n_players}</strong> perform better on Asian tables "
                f"({clarity})."
            )
        bullets.append(
            f"<strong>{pool_label} (China vs. Rest of World):</strong> "
            f"{chn_noun.title()} outperform rating expectations by "
            f"<strong>{kpis.chn_h2h_asian_resid_pp:+.1f}%</strong> "
            f"on Asian tables vs. "
            f"<strong>{kpis.chn_h2h_euro_resid_pp:+.1f}%</strong> "
            f"on European tables.{paired_clause}"
        )

    if (
        kpis.grippy_upset_resid_pp is not None
        and kpis.skiddy_upset_resid_pp is not None
        and kpis.upset_resid_gap_pp is not None
    ):
        direction = (
            f"<strong>{abs(kpis.upset_resid_gap_pp):.1f}% more upsets</strong> "
            "(vs. rating expectations) on European tables than on Asian tables"
            if kpis.upset_resid_gap_pp > 0
            else "similar upset rates on Asian and European tables"
        )
        bullets.append(
            f"<strong>{pool_label} upsets:</strong> Clear underdogs (&lt;40% pre-match "
            f"chance) pull off {direction} (Asian: "
            f"<strong>{kpis.grippy_upset_resid_pp:+.1f}%</strong> vs. European: "
            f"<strong>{kpis.skiddy_upset_resid_pp:+.1f}%</strong>)."
        )

    bullets.append(
        "<strong>Reading these numbers:</strong> <code>0%</code> means winning "
        "right on schedule based on player ratings; <code>+5%</code> means "
        "winning 5 percentage points more often than expected."
    )
    _render_insight_bullets(bullets, box_class="tt-summary-box")


def _render_paired_callout(
    paired: PairedContrastSummary,
    *,
    h2h_only: bool,
    chn_noun: str,
) -> None:
    scope_lbl = (
        "against non-Chinese opponents only" if h2h_only else "across all rated matches"
    )
    if paired.n_players == 0 or paired.mean_diff_pp is None:
        st.info(
            f"Not enough {chn_noun} with matches on both Asian and European "
            "tables under the current filters."
        )
        return

    ci_txt = f"±{paired.ci95_pp:.2f}" if paired.ci95_pp is not None else "n/a"
    pct_txt = (
        f"{paired.pct_better_on_asian:.1f}%"
        if paired.pct_better_on_asian is not None
        else "\u2014"
    )
    st.markdown(
        f"""
        <div class="tt-callout">
        <strong>Paired Within-Player Test ({chn_noun.title()}, {scope_lbl}):</strong>
        Across <strong>{paired.n_players}</strong> {chn_noun} with ≥5 matches on
        both surface groups, their average <em>(Asian \u2212 European)</em> shift is
        <strong>{paired.mean_diff_pp:+.2f}%</strong> ({ci_txt}% 95% CI,
        paired <em>p</em> = <code>{_format_p_val(paired.p_value)}</code>).
        <strong>{paired.n_better_on_asian} of {paired.n_players}</strong> ({pct_txt})
        do better on Asian/Grippy tables than on European/Skiddy tables.
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_tilt_summary(tilt: TiltAnalysisResult, *, players_noun: str) -> None:
    if tilt.total_players == 0:
        st.info(
            f"No {players_noun} meet the minimum match count on both surfaces under "
            "the current filters."
        )
        return

    chn_share = (
        f"{tilt.chn_asian_share_pct:.1f}%"
        if tilt.chn_asian_share_pct is not None
        else "\u2014"
    )
    non_share = (
        f"{tilt.non_chn_asian_share_pct:.1f}%"
        if tilt.non_chn_asian_share_pct is not None
        else "\u2014"
    )
    or_txt = f"{tilt.odds_ratio:.2f}" if tilt.odds_ratio is not None else "\u2014"
    neutral_note = (
        f" ({tilt.neutral_excluded} neutral within margin excluded)"
        if tilt.neutral_excluded > 0
        else ""
    )
    st.markdown(
        f"""
        <div class="tt-callout">
        <strong>2x2 Split ({tilt.total_players:,} {players_noun}{neutral_note}):
        </strong>
        <strong>{chn_share}</strong> of Chinese {players_noun} land in
        <em>Play Better on Asian Tables</em> vs. <strong>{non_share}</strong> of
        non-Chinese {players_noun} (Odds Ratio = <code>{or_txt}</code>,
        Fisher's exact <em>p</em> = <code>{_format_p_val(tilt.fisher_p_value)}</code>,
        Chi-square <em>p</em> = <code>{_format_p_val(tilt.chi2_p_value)}</code>;
        Welch's t-test on boost size <em>p</em> =
        <code>{_format_p_val(tilt.welch_p_value)}</code>).
        </div>
        """,
        unsafe_allow_html=True,
    )


def _describe_tilt_conclusion(
    tilt: TiltAnalysisResult,
    *,
    chn_noun: str,
    non_chn_noun: str,
) -> str:
    if tilt.total_players == 0:
        return (
            "Not enough players meet the match threshold on both surfaces to draw a "
            "conclusion under these filters."
        )

    chn_share = tilt.chn_asian_share_pct
    non_share = tilt.non_chn_asian_share_pct
    fp = tilt.fisher_p_value

    if chn_share is not None and non_share is not None:
        if chn_share > non_share:
            if fp is not None and fp < 0.05:
                headcount_txt = (
                    f"<strong>Yes, by headcount too:</strong> <strong>{chn_share:.1f}%"
                    f"</strong> of {chn_noun} play better on Asian tables vs. "
                    f"<strong>{non_share:.1f}%</strong> of {non_chn_noun}."
                )
            else:
                headcount_txt = (
                    f"By simple headcount, more {chn_noun} lean toward Asian tables "
                    f"(<strong>{chn_share:.1f}%</strong> vs. "
                    f"<strong>{non_share:.1f}%</strong> for {non_chn_noun}), though "
                    "headcount alone isn't decisive because small swings count the "
                    "same as large ones."
                )
        else:
            headcount_txt = (
                f"By headcount, <strong>{chn_share:.1f}%</strong> of {chn_noun} "
                f"play better on Asian tables vs. <strong>{non_share:.1f}%</strong> "
                f"of {non_chn_noun}."
            )
    else:
        headcount_txt = "Not enough players in both groups to compare headcount shares."

    if tilt.welch_diff_pp is not None and tilt.welch_p_value is not None:
        strength = (
            "statistically significant"
            if tilt.welch_p_value < 0.05
            else "a modest difference"
        )
        size_txt = (
            f"Looking at the <strong>size of the boost</strong> (right chart), "
            f"{chn_noun} show a <strong>{tilt.welch_diff_pp:+.1f}%</strong> "
            f"difference in Asian-table boost vs. {non_chn_noun} ({strength})."
        )
    else:
        size_txt = ""

    return f"{headcount_txt} {size_txt}".strip()


def _describe_upset_conclusion(
    kpis: SurfaceKPISummary,
    brand_matrix: pl.DataFrame,
    *,
    pool_label: str,
) -> str:
    if brand_matrix.is_empty():
        return "Not enough underdog matches under the current filters."

    valid = brand_matrix.filter(pl.col("upset_residual_pp").is_not_null())
    if valid.is_empty():
        return "Not enough underdog (<40%) matches to compare upset rates."

    hi_row = valid.sort("upset_residual_pp", descending=True).row(0, named=True)
    lo_row = valid.sort("upset_residual_pp", descending=False).row(0, named=True)

    gap_sentence = ""
    if (
        kpis.grippy_upset_resid_pp is not None
        and kpis.skiddy_upset_resid_pp is not None
    ):
        reliability = (
            ", meaning favorites hold up more reliably on grippier tables"
            if kpis.skiddy_upset_resid_pp > kpis.grippy_upset_resid_pp
            else ""
        )
        gap_sentence = (
            f"In {pool_label}, Asian (Grippy) tables see <strong>"
            f"{kpis.grippy_upset_resid_pp:+.1f}%</strong> upsets vs. expected, "
            f"compared to <strong>{kpis.skiddy_upset_resid_pp:+.1f}%</strong> on "
            f"European (Skiddy) tables{reliability}. "
        )

    return (
        f"{gap_sentence}Highest upset rate vs. expected: "
        f"<strong>{hi_row['group_name']} ({hi_row['upset_residual_pp']:+.1f}%)"
        f"</strong>; lowest: <strong>{lo_row['group_name']} "
        f"({lo_row['upset_residual_pp']:+.1f}%)</strong>."
    )


def _render_page() -> None:
    st.title("Table Types: Do Asian Tables Help Chinese Players?")
    st.caption(
        "How players perform on different tournament table brands and surfaces "
        "compared with pre-match Glicko-2 expectations. Men's and Women's "
        "Singles are analyzed separately."
    )

    if not data_files_exist():
        st.warning(
            "Parquet snapshots are missing in `app/data/`. "
            "Run `make app-refresh` to generate them."
        )
        return

    ratings_raw = load_glicko_match_ratings()
    events = load_events()
    players = load_players()
    table_brands = load_table_brands()

    # --- Prominent Top Men vs Women Toggle ---
    pool_choice = st.segmented_control(
        "Singles Tour",
        options=list(POOL_LABELS.keys()),
        default="Men's Singles",
        selection_mode="single",
        label_visibility="collapsed",
        width="stretch",
        key="ttv2_gender_choice",  # gitleaks:allow
    )
    pool_label = pool_choice if pool_choice in POOL_LABELS else "Men's Singles"
    gender = POOL_LABELS[pool_label]

    is_men = gender == "men"
    tour_color = GENDER_COLORS["Men"] if is_men else GENDER_COLORS["Women"]
    players_noun = "men" if is_men else "women"
    chn_noun = "Chinese men" if is_men else "Chinese women"
    non_chn_noun = "non-Chinese men" if is_men else "non-Chinese women"

    st.markdown(
        f'<div class="tt-tour-banner" style="border: 1px solid {tour_color}; '
        f'color: {tour_color};">Viewing: {pool_label} '
        "(separate rating pool)</div>",
        unsafe_allow_html=True,
    )

    # --- Clean, Minimal Filter Bar (No Date Range) ---
    f1, f2 = st.columns([1.2, 1.4], gap="medium")
    with f1:
        grouping_label = st.radio(
            "Show results by",
            options=list(GROUPING_LABELS.keys()),
            index=0,
            horizontal=True,
            key="ttv2_grouping_choice",
        )
        grouping = GROUPING_LABELS[grouping_label]
    with f2:
        sel_tiers = st.multiselect(
            "Tournament Tier (optional)",
            options=list(TIER_OPTIONS),
            default=[],
            placeholder="All tournaments (Grand Smash → Feeder & Majors)",
            key="ttv2_tiers",
        )

    with st.expander("Filter settings (minimum matches & newcomers)", expanded=False):
        s1, s2 = st.columns(2, gap="medium")
        with s1:
            min_brand_matches = st.slider(
                "Minimum matches per table brand",
                min_value=10,
                max_value=300,
                value=50,
                step=10,
                key="ttv2_min_brand_matches",
            )
        with s2:
            established_only = st.toggle(
                "Established players only (5+ prior matches each)",
                value=True,
                key="ttv2_established_only",
                help="Hides tour newcomers whose starting rating is still settling.",
            )

    min_prior = 5 if established_only else 0
    surface_matches, player_matches = _cached_surface_frames(
        ratings_raw.height,
        events.height,
        players.height,
        table_brands.height,
        gender,
        tuple(sel_tiers),
        min_prior,
    )

    if surface_matches.is_empty():
        st.info(f"No {pool_label} matches match the current filter selection.")
        return

    kpis = compute_kpi_summary(surface_matches, player_matches, min_matches_paired=5)

    # --- Top KPI Strip (Explicitly Labeled Men's or Women's) ---
    k1, k2, k3, k4 = st.columns(4, gap="medium")
    with k1:
        st.metric(
            f"{pool_label} Matches",
            f"{kpis.total_matches:,}",
            f"Across {kpis.total_events:,} tournaments",
        )
    with k2:
        st.metric(
            "Most Used Table",
            kpis.top_sponsor or "\u2014",
            (
                f"{kpis.top_sponsor_events:,} events · "
                f"{kpis.top_sponsor_matches:,} matches"
            ),
        )
    with k3:
        st.metric(
            f"{chn_noun.title()}: Asian Table Edge",
            _format_pct(kpis.chn_h2h_gap_pp),
            "Extra win % on Asian vs. European tables",
        )
    with k4:
        st.metric(
            f"{pool_label}: Extra Upsets on Euro Tables",
            _format_pct(kpis.upset_resid_gap_pp),
            "Compared to Asian tables (<40% underdogs)",
        )

    paired_default = compute_paired_chn_contrast(
        player_matches,
        min_matches_per_surface=5,
        chn_h2h_only=True,
    )
    _render_top_takeaways(
        kpis,
        paired_default,
        pool_label=pool_label,
        chn_noun=chn_noun,
    )

    st.markdown('<hr class="tt-divider" />', unsafe_allow_html=True)

    # --- Section 1: Do Chinese Players Win More on Asian Tables? ---
    st.subheader(f"1. {pool_label}: Do {chn_noun.title()} Win More on Asian Tables?")

    s1_ctrl, _s1_spacer = st.columns([1.5, 2.0])
    with s1_ctrl:
        chn_h2h_only = st.toggle(
            "Hide China-vs-China matches (recommended)",
            value=True,
            key="ttv2_chn_h2h_only",
            help=(
                "When two Chinese players play each other, one wins and one loses, "
                "which cancels out any table effect."
            ),
        )

    paired_chn = compute_paired_chn_contrast(
        player_matches,
        min_matches_per_surface=5,
        chn_h2h_only=chn_h2h_only,
    )

    if (
        kpis.chn_h2h_asian_resid_pp is not None
        and kpis.chn_h2h_euro_resid_pp is not None
        and chn_h2h_only
    ):
        s1_conclusion = (
            f"Against non-Chinese opponents, {chn_noun} beat rating expectations by "
            f"<strong>{kpis.chn_h2h_asian_resid_pp:+.1f}%</strong> on Asian tables "
            f"vs. <strong>{kpis.chn_h2h_euro_resid_pp:+.1f}%</strong> on European "
            f"tables, while {non_chn_noun} stay right near <code>0%</code>."
        )
    elif paired_chn.mean_diff_pp is not None:
        s1_conclusion = (
            f"Across the same {paired_chn.n_players} {chn_noun} on both surfaces, "
            f"their average shift is <strong>{paired_chn.mean_diff_pp:+.1f}%</strong> "
            "on Asian vs. European tables."
        )
    else:
        s1_conclusion = (
            f"Switch between Asian vs. European tables and individual brands above "
            f"to inspect {pool_label} win rates."
        )

    _render_insight_bullets(
        [
            "<strong>Reading the chart:</strong> Dots to the right of <code>0%</code> "
            "mean players won more often than ratings predicted; dots to the left "
            "mean fewer wins. Horizontal bars show the 95% margin of error (wider "
            "bars = smaller sample).",
            f"<strong>{pool_label} takeaway:</strong> {s1_conclusion}",
        ]
    )

    cohort_df = build_cohort_residuals(
        player_matches,
        grouping=grouping,
        chn_h2h_only=chn_h2h_only,
        min_matches=min_brand_matches,
    )
    if cohort_df.is_empty():
        st.info(
            f"Not enough {pool_label} matches for the selected grouping and filter."
        )
    else:
        forest_title = (
            "Win Rate vs. Expected: Asian vs. European Tables"
            if grouping == "surface"
            else "Win Rate vs. Expected by Table Brand"
        )
        render_chart(
            build_residual_forest_plot(
                cohort_df,
                title=forest_title,
                pool_label=pool_label,
            ),
            key="ttv2_forest_plot",
        )

    with st.expander(
        f"Technical details & individual {chn_noun} numbers", expanded=False
    ):
        _render_paired_callout(
            paired_chn,
            h2h_only=chn_h2h_only,
            chn_noun=chn_noun,
        )
        if not paired_chn.players_df.is_empty():
            st.dataframe(
                paired_chn.players_df.select(
                    "flag_url",
                    "player_name",
                    "grippy_matches",
                    "grippy_resid_pp",
                    "skiddy_matches",
                    "skiddy_resid_pp",
                    "tilt_pp",
                ).to_pandas(),
                hide_index=True,
                width="stretch",
                height=300,
                column_config={
                    "flag_url": st.column_config.ImageColumn("Flag", width="small"),
                    "player_name": st.column_config.TextColumn(
                        "Player", width="medium"
                    ),
                    "grippy_matches": st.column_config.NumberColumn(
                        "Asian M", format="%d"
                    ),
                    "grippy_resid_pp": st.column_config.NumberColumn(
                        "Asian vs Exp", format="%+.1f%%"
                    ),
                    "skiddy_matches": st.column_config.NumberColumn(
                        "Euro M", format="%d"
                    ),
                    "skiddy_resid_pp": st.column_config.NumberColumn(
                        "Euro vs Exp", format="%+.1f%%"
                    ),
                    "tilt_pp": st.column_config.NumberColumn(
                        "Asian \u2212 Euro", format="%+.1f%%"
                    ),
                },
            )

    st.markdown('<hr class="tt-divider" />', unsafe_allow_html=True)

    # --- Section 2: Player Grouping Approach ("Which Group Is More Chinese?") ---
    st.subheader(
        f"2. {pool_label}: If We Group Players by Best Table, "
        "Is One Group More Chinese?"
    )

    g1, g2, g3 = st.columns([1.1, 1.1, 1.2], gap="medium")
    with g1:
        tilt_min_matches = st.slider(
            f"Min {pool_label} matches on EACH surface",
            min_value=3,
            max_value=30,
            value=10,
            step=1,
            key="ttv2_tilt_min_matches",
        )
    with g2:
        dead_zone_pp = st.slider(
            "Ignore near-ties within (\u00b1%)",
            min_value=0.0,
            max_value=10.0,
            value=0.0,
            step=1.0,
            key="ttv2_tilt_dead_zone",
            help="Skip players whose Asian vs. European difference is tiny.",
        )
    with g3:
        tilt_h2h_only = st.toggle(
            "For Chinese players, hide China-vs-China",
            value=False,
            key="ttv2_tilt_h2h_only",
        )

    tilt_res = build_player_tilt_analysis(
        player_matches,
        min_matches_per_surface=tilt_min_matches,
        dead_zone_pp=dead_zone_pp,
        chn_h2h_only=tilt_h2h_only,
    )

    tilt_conclusion = _describe_tilt_conclusion(
        tilt_res,
        chn_noun=chn_noun,
        non_chn_noun=non_chn_noun,
    )
    _render_insight_bullets(
        [
            f"<strong>How players are grouped:</strong> Every player with at least "
            f"<strong>{tilt_min_matches}</strong> matches on both surfaces is placed "
            "into 'Play Better on Asian Tables' or 'Play Better on European Tables'. "
            "The <strong>left chart</strong> shows how much of each group is Chinese; "
            "the <strong>right chart</strong> shows the average Asian-table boost.",
            f"<strong>Result:</strong> {tilt_conclusion}",
        ]
    )

    if tilt_res.total_players == 0:
        st.info(
            f"No {players_noun} meet the minimum match count on both surfaces under "
            "the current filters."
        )
    else:
        fig_share, fig_diff = build_tilt_comparison_charts(
            tilt_res,
            pool_label=pool_label,
        )
        tc1, tc2 = st.columns(2, gap="large")
        with tc1:
            render_chart(fig_share, key="ttv2_tilt_share_chart")
        with tc2:
            render_chart(fig_diff, key="ttv2_tilt_diff_chart")

        with st.expander(
            f"Technical details, 2x2 table & {pool_label} player list",
            expanded=False,
        ):
            _render_tilt_summary(tilt_res, players_noun=players_noun)
            cont_pd = (
                tilt_res.contingency_df.with_columns(
                    (
                        pl.col("pct_chn_ci_low").round(1).cast(pl.String)
                        + pl.lit("% - ")
                        + pl.col("pct_chn_ci_high").round(1).cast(pl.String)
                        + pl.lit("%")
                    ).alias("wilson_95_ci")
                )
                .select(
                    "tilt_group",
                    "total_players",
                    "chn_players",
                    "non_chn_players",
                    "pct_chn",
                    "wilson_95_ci",
                )
                .to_pandas()
            )
            st.dataframe(
                cont_pd,
                hide_index=True,
                width="stretch",
                column_config={
                    "tilt_group": st.column_config.TextColumn(
                        "Player Group", width="medium"
                    ),
                    "total_players": st.column_config.NumberColumn(
                        "Players", format="%d"
                    ),
                    "chn_players": st.column_config.NumberColumn("CHN", format="%d"),
                    "non_chn_players": st.column_config.NumberColumn(
                        "Non-CHN", format="%d"
                    ),
                    "pct_chn": st.column_config.NumberColumn(
                        "% Chinese", format="%.1f%%"
                    ),
                    "wilson_95_ci": st.column_config.TextColumn("95% Wilson CI"),
                },
            )
            st.dataframe(
                tilt_res.players_df.select(
                    "flag_url",
                    "player_name",
                    "org",
                    "tilt_group",
                    "tilt_pp",
                    "grippy_matches",
                    "grippy_resid_pp",
                    "skiddy_matches",
                    "skiddy_resid_pp",
                ).to_pandas(),
                hide_index=True,
                width="stretch",
                height=340,
                column_config={
                    "flag_url": st.column_config.ImageColumn("Flag", width="small"),
                    "player_name": st.column_config.TextColumn(
                        "Player", width="medium"
                    ),
                    "org": st.column_config.TextColumn("Org", width="small"),
                    "tilt_group": st.column_config.TextColumn("Group", width="medium"),
                    "tilt_pp": st.column_config.NumberColumn(
                        "Asian \u2212 Euro", format="%+.1f%%"
                    ),
                    "grippy_matches": st.column_config.NumberColumn(
                        "Asian M", format="%d"
                    ),
                    "grippy_resid_pp": st.column_config.NumberColumn(
                        "Asian vs Exp", format="%+.1f%%"
                    ),
                    "skiddy_matches": st.column_config.NumberColumn(
                        "Euro M", format="%d"
                    ),
                    "skiddy_resid_pp": st.column_config.NumberColumn(
                        "Euro vs Exp", format="%+.1f%%"
                    ),
                },
            )

    st.markdown('<hr class="tt-divider" />', unsafe_allow_html=True)

    # --- Section 3: Table Brand Summary & Upset Rate Matrix ---
    st.subheader(f"3. {pool_label}: Which Tables Lead to More Upsets?")

    brand_matrix = build_brand_summary_matrix(
        surface_matches,
        player_matches,
        min_matches=min_brand_matches,
        grouping=grouping,
    )

    _render_insight_bullets(
        [
            "<strong>Reading the upset chart:</strong> Focuses on clear underdogs "
            "(&lt;40% pre-match win chance). Dots left of <code>0%</code> mean "
            "fewer upsets (favorites win reliably); dots right of <code>0%</code> "
            "mean more upsets than ratings predicted.",
            f"<strong>Key pattern:</strong> "
            f"{_describe_upset_conclusion(kpis, brand_matrix, pool_label=pool_label)}",
        ]
    )

    if brand_matrix.is_empty():
        st.info(f"No table brands meet the minimum {pool_label} match threshold.")
    else:
        render_chart(
            build_upset_residual_chart(brand_matrix, pool_label=pool_label),
            key="ttv2_upset_residual_chart",
        )

        with st.expander(
            f"Full {pool_label} table brand numbers (matches, rating gaps & upsets)",
            expanded=False,
        ):
            matrix_pd = brand_matrix.select(
                "group_name",
                "surface_profile",
                "events",
                "matches",
                "avg_rating_gap",
                "underdog_40_matches",
                "upset_rate_pct",
                "expected_upset_pct",
                "upset_residual_pp",
                "chn_h2h_residual_pp",
                "non_chn_residual_pp",
            ).to_pandas()

            first_col_label = (
                "Surface Profile" if grouping == "surface" else "Table Brand"
            )
            matrix_pd = matrix_pd.rename(
                columns={
                    "group_name": first_col_label,
                    "surface_profile": "Surface Group",
                    "events": "Events",
                    "matches": "Matches (n)",
                    "avg_rating_gap": "Avg Rating Gap",
                    "underdog_40_matches": "Underdog (<40%) n",
                    "upset_rate_pct": "Upset Rate %",
                    "expected_upset_pct": "Expected Upset %",
                    "upset_residual_pp": "Upsets vs Exp",
                    "chn_h2h_residual_pp": "CHN vs Non-CHN",
                    "non_chn_residual_pp": "Non-CHN vs Exp",
                }
            )
            if grouping == "surface":
                matrix_pd = matrix_pd.drop(columns=["Surface Group"])

            resid_cols = ["Upsets vs Exp", "CHN vs Non-CHN", "Non-CHN vs Exp"]
            styled_matrix = matrix_pd.style.format(
                {
                    "Events": "{:,}",
                    "Matches (n)": "{:,}",
                    "Avg Rating Gap": "{:.1f}",
                    "Underdog (<40%) n": "{:,}",
                    "Upset Rate %": lambda v: "\u2014" if pd.isna(v) else f"{v:.1f}%",
                    "Expected Upset %": lambda v: (
                        "\u2014" if pd.isna(v) else f"{v:.1f}%"
                    ),
                    "Upsets vs Exp": lambda v: "\u2014" if pd.isna(v) else f"{v:+.1f}%",
                    "CHN vs Non-CHN": lambda v: (
                        "\u2014" if pd.isna(v) else f"{v:+.1f}%"
                    ),
                    "Non-CHN vs Exp": lambda v: (
                        "\u2014" if pd.isna(v) else f"{v:+.1f}%"
                    ),
                }
            ).map(_diverging_residual_css, subset=resid_cols)
            st.dataframe(styled_matrix, hide_index=True, width="stretch")

    st.markdown('<hr class="tt-divider" />', unsafe_allow_html=True)

    # --- Section 4: Player Surface Boosts & Anti-Specialists ---
    st.subheader(f"4. {pool_label}: Which Individual Players Get the Biggest Boost?")

    _render_insight_bullets(
        [
            f"<strong>Individual rankings:</strong> Sorts {players_noun} by how "
            "much more (<code>+</code>) or less (<code>-</code>) often they win "
            "compared to rating expectations on a chosen brand or surface.",
            "<strong>Watch the margin of error (<code>\u00b195% CI</code>):</strong> "
            "Single-player samples are smaller. When the error range is wider "
            "than the boost itself, the difference may just be match-to-match "
            "variance.",
        ]
    )

    brand_choices = [
        str(r["table_sponsor"])
        for r in surface_matches.group_by("table_sponsor")
        .agg(pl.len().alias("n"))
        .sort("n", descending=True)
        .to_dicts()
    ]
    scope_options = [
        "Asian vs European Boost",
        SURFACE_GRIPPY,
        SURFACE_SKIDDY,
        *brand_choices,
    ]

    p1, p2, p3, p4 = st.columns([1.3, 0.9, 1.0, 1.1], gap="medium")
    with p1:
        target_scope = st.selectbox(
            "Surface or Table Brand",
            options=scope_options,
            index=0,
            key="ttv2_player_target_scope",
        )
    with p2:
        player_min_matches = st.slider(
            "Min matches",
            min_value=5,
            max_value=50,
            value=15,
            step=1,
            key="ttv2_player_min_matches",
        )
    with p3:
        origin_choice = st.selectbox(
            "Country Filter",
            options=["All", "China (CHN)", "Non-China"],
            index=0,
            key="ttv2_player_origin",
        )
    with p4:
        sort_choice = st.selectbox(
            "Sort By",
            options=["Overperformers", "Underperformers", "Largest Magnitude"],
            index=0,
            key="ttv2_player_sort",
        )

    search_placeholder = (
        "e.g. Wang Chuqin, Ma Long, Lebrun..."
        if is_men
        else "e.g. Sun Yingsha, Chen Meng, Hayata..."
    )
    search_q = st.text_input(
        f"Search {players_noun}",
        placeholder=search_placeholder,
        key="ttv2_player_search",
    )

    boosts_df = build_player_surface_boosts(
        player_matches,
        target_scope=target_scope,
        min_matches=player_min_matches,
        origin_filter=origin_choice,  # type: ignore[arg-type]
        search_query=search_q,
        chn_h2h_only=chn_h2h_only,
        sort_mode=sort_choice,  # type: ignore[arg-type]
    )

    if boosts_df.is_empty():
        st.info(f"No {players_noun} meet the selected surface and match-count filters.")
    else:
        edge_label = (
            "Asian \u2212 Euro Boost"
            if target_scope == "Asian vs European Boost"
            else "Win % vs Expected"
        )
        display_boosts = boosts_df.select(
            "rank",
            "flag_url",
            "player_name",
            "org",
            "matches",
            "actual_wl",
            "expected_wins",
            "win_pct_over_expected",
            "ci95_pp",
        ).to_pandas()

        st.dataframe(
            display_boosts,
            hide_index=True,
            width="stretch",
            height=520,
            column_config={
                "rank": st.column_config.NumberColumn("#", format="%d", width=52),
                "flag_url": st.column_config.ImageColumn("Flag", width="small"),
                "player_name": st.column_config.TextColumn("Player", width="medium"),
                "org": st.column_config.TextColumn("Org", width="small"),
                "matches": st.column_config.NumberColumn("Matches", format="%d"),
                "actual_wl": st.column_config.TextColumn("Actual W-L", width="small"),
                "expected_wins": st.column_config.NumberColumn(
                    "Expected W", format="%.1f"
                ),
                "win_pct_over_expected": st.column_config.NumberColumn(
                    edge_label, format="%+.1f%%"
                ),
                "ci95_pp": st.column_config.NumberColumn(
                    "\u00b195% CI", format="\u00b1%.1f%%"
                ),
            },
        )

    st.markdown('<hr class="tt-divider" />', unsafe_allow_html=True)

    brand_groups = _cached_brand_groups(table_brands.height)
    asian_brands = ", ".join(brand_groups["Asian"]) or "None"
    euro_brands = ", ".join(brand_groups["European"]) or "None"
    other_brands = ", ".join(brand_groups["Other"])
    other_clause = f"{other_brands}" if other_brands else "Nittaku, San-Ei, Xiom"

    with st.expander("Methodology & Data Notes", expanded=False):
        st.markdown(
            "1. **Why approved tables play differently (ITTF Law 2.01 & "
            "Technical Leaflet T1):** ITTF certification tests vertical bounce "
            "height (23-26 cm from a 30 cm drop) and low-speed sliding friction "
            "(\u03bc \u2264 0.6), but approved tables can still grip heavy topspin "
            "and backspin differently (Kamijima & Ushiyama, ITTF Sports Science "
            "Congress, 2011/2013).\n"
            "2. **Expected win rate baseline:** Computed from pre-match Glicko-2 "
            "win probabilities in the unified senior singles pool (separately for "
            "Men's Singles and Women's Singles), controlling for opponent strength.\n"
            "3. **Why China-vs-China matches are hidden by default:** When two "
            "Chinese players meet, one wins and one loses, pulling their combined "
            "average toward 0%.\n"
            "4. **Table brand groupings:**\n"
            f"   - **Asian Tables (Grippy):** {asian_brands} "
            "(**Butterfly is included here for its grippier surface**)\n"
            f"   - **European Tables (Skiddy):** {euro_brands} "
            "(**Stag is assumed to be European-style even though it is made in "
            "India**)\n"
            f"   - **Other Tables:** {other_clause} (plus events without a known "
            "table sponsor)"
        )


_render_page()

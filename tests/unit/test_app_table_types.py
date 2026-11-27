"""Unit and UI tests for Table Types win-rate residuals, upsets, and China H0."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest
from app.lib.table_types import (
    SURFACE_GRIPPY,
    SURFACE_OTHER,
    SURFACE_SKIDDY,
    build_brand_summary_matrix,
    build_cohort_residuals,
    build_player_surface_boosts,
    build_player_tilt_analysis,
    build_residual_forest_plot,
    build_tilt_comparison_charts,
    build_upset_residual_chart,
    chi_square_2x2_p_value,
    classify_surface_profile,
    compute_kpi_summary,
    compute_paired_chn_contrast,
    fisher_exact_two_tailed,
    map_event_tier,
    prepare_surface_matches,
    student_t_two_tailed_p_value,
    summarize_brand_groups,
    unpivot_surface_player_matches,
    welch_t_test,
    wilson_ci_95,
)
from streamlit.testing.v1 import AppTest

from tt_stats.analytics.export_app_data import default_output_dir
from tt_stats.core.config import project_root


def _sample_players() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "player_sk": [1, 2, 3, 4],
            "player_name": [
                "Wei ZHANG",
                "Mei LIN",
                "Lukas WEBER",
                "Alex MARTIN",
            ],
            "organization_code": ["CHN", "CHN", "GER", "FRA"],
            "country_code": ["CHN", "CHN", "GER", "FRA"],
            "organization_name": ["China", "China", "Germany", "France"],
            "country_name": ["China", "China", "Germany", "France"],
            "style": ["Attacker", "Attacker", "Attacker", "Defender"],
            "grip": ["Shakehand", "Shakehand", "Shakehand", "Shakehand"],
            "hand": ["Left", "Right", "Right", "Right"],
        }
    )


def _sample_events() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "event_sk": [10, 11, 12],
            "event_name": [
                "Singapore Smash",
                "WTT Contender Zagreb",
                "Japan Open",
            ],
            "event_type": ["WTT Grand Smash", "WTT Contender", "WTTC"],
            "table_sponsor": ["DHS", "Tibhar", "Nittaku"],
        }
    )


def _sample_brands() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "table_sponsor": ["DHS", "Tibhar", "Nittaku"],
            "table_group": ["Asian", "European", "Other"],
        }
    )


def _sample_ratings() -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    match_id = 1
    # 4 DHS (Grippy) matches:
    # - 2x CHN (1) beats GER (3) with expected 0.70 (+0.30 residual each)
    # - 1x CHN (1) beats CHN (2) (zero-sum intra-CHN, excluded when h2h_only=True)
    # - 1x GER (3) upsets FRA (4) when GER expected 0.30 (<40% underdog wins!)
    dhs_specs = [
        (1, 3, 0.70, 1.0, date(2024, 3, 1)),
        (1, 3, 0.70, 1.0, date(2024, 3, 2)),
        (1, 2, 0.50, 1.0, date(2024, 3, 3)),
        (3, 4, 0.30, 1.0, date(2024, 3, 4)),
    ]
    for p1, p2, exp, act, dt in dhs_specs:
        rows.append(
            {
                "match_sk": match_id,
                "event_sk": 10,
                "gender_category": "men",
                "rating_date": dt,
                "p1_player_sk": p1,
                "p2_player_sk": p2,
                "p1_pre_rating": 1800.0 if exp > 0.5 else 1600.0,
                "p2_pre_rating": 1650.0 if exp > 0.5 else 1750.0,
                "p1_expected_win_prob": exp,
                "p1_actual_score": act,
            }
        )
        match_id += 1

    # 4 Tibhar (Skiddy) matches:
    # - 2x CHN (1) loses to FRA (4) with expected 0.70 (-0.70 residual for CHN)
    # - 2x GER (3) beats FRA (4) with expected 0.60 (+0.40 residual for GER)
    tibhar_specs = [
        (1, 4, 0.70, 0.0, date(2024, 6, 1)),
        (1, 4, 0.70, 0.0, date(2024, 6, 2)),
        (3, 4, 0.60, 1.0, date(2024, 6, 3)),
        (3, 4, 0.60, 1.0, date(2024, 6, 4)),
    ]
    for p1, p2, exp, act, dt in tibhar_specs:
        rows.append(
            {
                "match_sk": match_id,
                "event_sk": 11,
                "gender_category": "men",
                "rating_date": dt,
                "p1_player_sk": p1,
                "p2_player_sk": p2,
                "p1_pre_rating": 1800.0 if exp > 0.5 else 1650.0,
                "p2_pre_rating": 1650.0 if exp > 0.5 else 1800.0,
                "p1_expected_win_prob": exp,
                "p1_actual_score": act,
            }
        )
        match_id += 1

    return pl.DataFrame(rows)


def test_taxonomy_and_stat_helpers() -> None:
    assert map_event_tier("WTT Grand Smash") == "Grand Smash"
    assert map_event_tier("WTT Feeder") == "Feeder"
    assert map_event_tier("Olympic Games") == "ITTF Majors / Other"
    assert map_event_tier(None) == "ITTF Majors / Other"

    assert classify_surface_profile("DHS", "Asian") == SURFACE_GRIPPY
    assert classify_surface_profile("Tibhar", "European") == SURFACE_SKIDDY
    assert classify_surface_profile("Nittaku", "Other") == SURFACE_OTHER
    assert classify_surface_profile("Butterfly", None) == SURFACE_GRIPPY

    lo, hi = wilson_ci_95(5, 10)
    assert lo < 50.0 < hi
    assert wilson_ci_95(0, 0) == (0.0, 0.0)

    # Classic Fisher exact test example: [[1, 9], [11, 3]] -> p ≈ 0.002759
    p_fisher = fisher_exact_two_tailed(1, 9, 11, 3)
    assert p_fisher is not None
    assert p_fisher == pytest.approx(0.0027594, abs=1e-4)

    p_chi2 = chi_square_2x2_p_value(20, 5, 5, 20)
    assert p_chi2 is not None
    assert p_chi2 < 0.001


def test_prepare_and_cohort_h2h_isolation() -> None:
    players = _sample_players()
    events = _sample_events()
    brands = _sample_brands()
    ratings = _sample_ratings()

    sm = prepare_surface_matches(
        ratings,
        events,
        players,
        brands,
        gender="men",
        min_prior_matches=0,
    )
    assert sm.height == 8
    # 2 CHN-vs-GER on DHS + 2 CHN-vs-FRA on Tibhar = 4 CHN vs Non-CHN matches
    assert sm.filter(pl.col("is_chn_vs_non_chn")).height == 4
    assert sm.filter(pl.col("p1_is_chn") & pl.col("p2_is_chn")).height == 1

    pm = unpivot_surface_player_matches(sm, players)
    assert pm.height == 16

    cohorts_h2h = build_cohort_residuals(
        pm,
        grouping="surface",
        chn_h2h_only=True,
        min_matches=1,
    )
    chn_grippy = cohorts_h2h.filter(
        (pl.col("group_label") == SURFACE_GRIPPY)
        & pl.col("cohort").str.starts_with("China")
    ).row(0, named=True)
    # Two CHN-vs-GER wins at 0.70 exp -> +30.0 pp residual (intra-CHN excluded)
    assert chn_grippy["n"] == 2
    assert chn_grippy["residual_pp"] == pytest.approx(30.0)

    chn_skiddy = cohorts_h2h.filter(
        (pl.col("group_label") == SURFACE_SKIDDY)
        & pl.col("cohort").str.starts_with("China")
    ).row(0, named=True)
    assert chn_skiddy["n"] == 2
    assert chn_skiddy["residual_pp"] == pytest.approx(-70.0)

    paired = compute_paired_chn_contrast(
        pm,
        min_matches_per_surface=2,
        chn_h2h_only=True,
    )
    assert paired.n_players == 1
    assert paired.mean_diff_pp == pytest.approx(100.0)
    assert paired.n_better_on_asian == 1


def test_player_tilt_grouping_upset_matrix_and_boosts() -> None:
    players = _sample_players()
    events = _sample_events()
    brands = _sample_brands()
    ratings = _sample_ratings()

    sm = prepare_surface_matches(
        ratings,
        events,
        players,
        brands,
        min_prior_matches=0,
    )
    pm = unpivot_surface_player_matches(sm, players)

    # Players 1 (CHN: +30 vs -70 -> Grippy) and 3 (GER: +15 vs +40 -> Skiddy).
    # Player 4 has only 1 DHS match, so excluded at min_matches_per_surface=2.
    tilt = build_player_tilt_analysis(
        pm,
        min_matches_per_surface=2,
        dead_zone_pp=0.0,
        chn_h2h_only=True,
    )
    assert tilt.total_players == 2
    assert tilt.chn_asian_share_pct == pytest.approx(100.0)
    assert tilt.non_chn_asian_share_pct == pytest.approx(0.0)
    assert tilt.fisher_p_value == pytest.approx(1.0)

    matrix = build_brand_summary_matrix(sm, pm, min_matches=1, grouping="brand")
    dhs = matrix.filter(pl.col("group_name") == "DHS").row(0, named=True)
    assert dhs["matches"] == 4
    # Three <40% underdog matches on DHS: 2x (1 vs 3) favored won, 1x (3 vs 4) upset.
    assert dhs["underdog_40_matches"] == 3
    assert dhs["upset_rate_pct"] == pytest.approx(100.0 / 3.0)
    assert dhs["expected_upset_pct"] == pytest.approx(30.0)
    assert dhs["upset_residual_pp"] == pytest.approx(100.0 / 3.0 - 30.0)

    boosts = build_player_surface_boosts(
        pm,
        target_scope="Asian vs European Boost",
        min_matches=2,
        chn_h2h_only=True,
    )
    assert boosts["player_name"].to_list() == ["Wei ZHANG", "Lukas WEBER"]
    assert boosts.row(0, named=True)["win_pct_over_expected"] == pytest.approx(100.0)

    dhs_boosts = build_player_surface_boosts(
        pm,
        target_scope="DHS",
        min_matches=2,
        origin_filter="China (CHN)",
        search_query="wei",
        chn_h2h_only=True,
    )
    assert dhs_boosts.height == 1
    assert dhs_boosts.row(0, named=True)["actual_wl"] == "2-0"

    kpis = compute_kpi_summary(sm, pm, min_matches_paired=2)
    assert kpis.total_matches == 8
    assert kpis.total_events == 2

    # Charts build without errors
    fig_forest = build_residual_forest_plot(
        build_cohort_residuals(pm, grouping="brand", min_matches=1)
    )
    fig_share, fig_diff = build_tilt_comparison_charts(tilt)
    fig_upset = build_upset_residual_chart(matrix)
    assert len(fig_forest.data) == 2
    assert len(fig_share.data) == 1
    assert len(fig_diff.data) == 1
    assert len(fig_upset.data) == 2


def test_empty_inputs_return_empty_schemas() -> None:
    empty_ratings = _sample_ratings().head(0)
    empty_sm = prepare_surface_matches(
        empty_ratings,
        _sample_events(),
        _sample_players(),
        _sample_brands(),
    )
    empty_pm = unpivot_surface_player_matches(empty_sm, _sample_players())
    assert empty_sm.is_empty()
    assert empty_pm.is_empty()
    assert build_cohort_residuals(empty_pm).is_empty()
    assert build_brand_summary_matrix(empty_sm, empty_pm).is_empty()
    assert build_player_surface_boosts(empty_pm).is_empty()
    assert compute_kpi_summary(empty_sm, empty_pm).total_matches == 0
    assert compute_paired_chn_contrast(empty_pm).n_players == 0
    assert build_player_tilt_analysis(empty_pm).total_players == 0


def test_real_parquet_smoke() -> None:
    data_dir = default_output_dir()
    ratings = pl.read_parquet(data_dir / "fct_glicko_match_ratings.parquet")
    events = pl.read_parquet(data_dir / "dim_events.parquet")
    players = pl.read_parquet(data_dir / "dim_players.parquet")
    brands = pl.read_parquet(data_dir / "dim_table_brands.parquet")

    sm = prepare_surface_matches(ratings, events, players, brands, min_prior_matches=5)
    pm = unpivot_surface_player_matches(sm, players)
    assert sm.height > 10_000

    kpis = compute_kpi_summary(sm, pm, min_matches_paired=5)
    assert kpis.chn_h2h_asian_resid_pp is not None
    assert kpis.chn_h2h_euro_resid_pp is not None
    assert kpis.chn_h2h_gap_pp is not None
    assert kpis.chn_h2h_gap_pp > 0.0
    assert kpis.upset_resid_gap_pp is not None
    assert kpis.upset_resid_gap_pp > 0.0

    tilt = build_player_tilt_analysis(pm, min_matches_per_surface=10)
    assert tilt.total_players > 100
    assert tilt.fisher_p_value is not None


def test_table_types_page_apptest(monkeypatch: pytest.MonkeyPatch) -> None:
    app_dir = project_root() / "app"
    monkeypatch.syspath_prepend(str(app_dir))
    page_path = app_dir / "pages" / "7_Table_Types.py"
    at = AppTest.from_file(page_path).run(timeout=30)
    assert not at.exception
    assert len(at.date_input) == 0
    assert len(at.metric) == 4
    assert len(at.dataframe) >= 3
    assert any("Men's Singles" in s.value for s in at.subheader)

    # Switch grouping to "By Table Brand" and switch tour to "Women's Singles"
    at.radio(key="ttv2_grouping_choice").set_value("By Table Brand").run(timeout=30)
    assert not at.exception
    at.segmented_control(key="ttv2_gender_choice").set_value(  # gitleaks:allow
        "Women's Singles"
    ).run(timeout=30)
    assert not at.exception
    assert any("Women's Singles" in s.value for s in at.subheader)

    # Switch player boost target to DHS and search
    at.selectbox(key="ttv2_player_target_scope").set_value("DHS").run(timeout=30)
    assert not at.exception
    at.text_input(key="ttv2_player_search").set_value("Sun").run(timeout=30)
    assert not at.exception


def test_summarize_brand_groups() -> None:
    brands = pl.DataFrame(
        {
            "table_sponsor": [
                " DHS ",
                "Butterfly",
                "Butterfly",
                "Tibhar",
                "Xiom",
                None,
            ],
            "table_group": ["Asian", "Asian", "Asian", "European", "Other", "Other"],
        }
    )
    assert summarize_brand_groups(brands) == {
        "Asian": ["Butterfly", "DHS"],
        "European": ["Tibhar"],
        "Other": ["Xiom"],
    }
    assert summarize_brand_groups(brands.head(0)) == {
        "Asian": [],
        "European": [],
        "Other": [],
    }


def test_student_t_and_welch_t_test() -> None:
    assert student_t_two_tailed_p_value(0.0, 10.0) == pytest.approx(1.0)
    assert student_t_two_tailed_p_value(2.22813885, 10.0) == pytest.approx(
        0.05, abs=1e-4
    )
    assert student_t_two_tailed_p_value(1.0, 0.0) is None

    diff, t_stat, p_val = welch_t_test([10.0, 11.0, 12.0, 10.5], [0.0, 1.0, 0.5, -0.5])
    assert diff is not None
    assert diff > 9.0
    assert t_stat is not None
    assert p_val is not None
    assert p_val < 0.001

    same_diff, _, same_p = welch_t_test([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    assert same_diff == pytest.approx(0.0)
    assert same_p == pytest.approx(1.0)

    assert welch_t_test([1.0], [2.0, 3.0]) == (None, None, None)

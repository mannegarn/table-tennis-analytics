"""Unit tests for Win Rates binomial models, logistic fit, aggregations, and UI."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest
from app.lib.win_rates import (
    binomial_match_probability,
    binomial_point_to_match_probability,
    build_combined_point_set_stats,
    build_format_win_rates,
    build_win_rate_scatter,
    compute_binomial_expected,
    filter_win_rate_rows,
    fit_logistic_curve,
    win_set_probability,
)
from streamlit.testing.v1 import AppTest

from tt_stats.core.config import project_root


def test_binomial_boundary_and_symmetry_invariants() -> None:
    grid = np.array([0.0, 0.25, 0.4, 0.5, 0.6, 0.75, 1.0], dtype=np.float64)

    set_probs = win_set_probability(grid)
    bo5_from_set = binomial_match_probability(grid, 5)
    bo7_from_set = binomial_match_probability(grid, 7)
    bo5_from_pt = binomial_point_to_match_probability(grid, 5)
    bo7_from_pt = binomial_point_to_match_probability(grid, 7)

    # Exact endpoints and 50/50 symmetry point
    for arr in (set_probs, bo5_from_set, bo7_from_set, bo5_from_pt, bo7_from_pt):
        assert arr[0] == pytest.approx(0.0, abs=1e-12)
        assert arr[3] == pytest.approx(0.5, abs=1e-12)
        assert arr[-1] == pytest.approx(1.0, abs=1e-12)
        # Symmetry: f(1 - p) == 1 - f(p)
        assert np.allclose(arr + arr[::-1], 1.0, atol=1e-12)

    # Leverage ordering for p > 0.5: Bo7 > Bo5 > Set > Point
    p_hi = 0.55
    s_hi = float(win_set_probability(p_hi))
    m5_hi = float(binomial_point_to_match_probability(p_hi, 5))
    m7_hi = float(binomial_point_to_match_probability(p_hi, 7))
    assert p_hi < s_hi < m5_hi < m7_hi < 1.0


def test_binomial_match_probability_rejects_even_best_of() -> None:
    with pytest.raises(ValueError, match="positive odd integer"):
        binomial_match_probability(0.5, 4)


def test_fit_logistic_curve_bounded_and_monotonic() -> None:
    xs = np.linspace(35.0, 65.0, 40, dtype=np.float64)
    ys = compute_binomial_expected(xs, mode="point_to_set")
    fit = fit_logistic_curve(xs, ys)

    assert fit is not None
    assert fit.rmse < 1.0
    assert np.all((fit.curve_y > 0.0) & (fit.curve_y < 100.0))
    assert np.all(np.diff(fit.curve_y) > 0.0)

    # Too few points returns None cleanly
    assert fit_logistic_curve(xs[:3], ys[:3]) is None


def test_aggregations_exclude_walkovers_and_separate_formats() -> None:
    players = pl.DataFrame(
        {
            "player_sk": [1, 2],
            "player_name": ["Alice CHEN", "Bob MULLER"],
            "organization_code": ["CHN", "GER"],
            "gender": ["F", "M"],
            "style": ["Defender", None],
            "grip": ["Shakehand", "Penhold"],
            "hand": ["Right", None],
        }
    )
    matches = pl.DataFrame(
        {
            "match_sk": [10, 11, 12, 13, 14],
            "p1_player_sk": [1, 1, 1, 1, 1],
            "p2_player_sk": [2, 2, 2, 2, 2],
            "winner_side": [1, 2, 1, 1, 1],
            "best_of": [5, 7, 5, 5, 5],
            "result_type": ["played", "played", "walkover", "injury", "retired"],
            "is_default_result": [False, False, True, True, True],
            "has_result": [True, True, True, True, True],
        }
    )
    games = pl.DataFrame(
        {
            "match_sk": [10, 10, 10, 11, 11, 11, 11],
            "p1_points": [11, 11, 11, 5, 5, 5, 5],
            "p2_points": [9, 9, 9, 11, 11, 11, 11],
            "winner_side": [1, 1, 1, 2, 2, 2, 2],
            "is_valid_game_score": [True, True, True, True, True, True, True],
        }
    )

    combined = build_combined_point_set_stats(matches, games, players)
    by_format = build_format_win_rates(matches, games, players)

    # Walkover, injury, and retired matches are completely excluded -> 2 played matches
    alice_comb = combined.filter(pl.col("player_sk") == 1).row(0, named=True)
    assert alice_comb["Matches"] == 2
    assert alice_comb["Sets Played"] == 7
    assert alice_comb["Sets Won"] == 3
    assert alice_comb["Points Won"] == 53
    assert alice_comb["Points Played"] == 124
    assert alice_comb["Style"] == "Defender"

    bob_comb = combined.filter(pl.col("player_sk") == 2).row(0, named=True)
    assert bob_comb["Style"] == "Unknown"
    assert bob_comb["Hand"] == "Unknown"

    # By-format keeps Bo5 and Bo7 strictly separate
    alice_bo5 = by_format.filter(
        (pl.col("player_sk") == 1) & (pl.col("Best Of") == 5)
    ).row(0, named=True)
    alice_bo7 = by_format.filter(
        (pl.col("player_sk") == 1) & (pl.col("Best Of") == 7)
    ).row(0, named=True)
    assert alice_bo5["Matches"] == 1
    assert alice_bo5["Win %"] == 100.0
    assert alice_bo7["Matches"] == 1
    assert alice_bo7["Win %"] == 0.0

    # Scatter builder runs cleanly and highlights Defender on top
    filtered = filter_win_rate_rows(by_format, best_of=5, min_matches=1)
    fig, metrics = build_win_rate_scatter(
        filtered,
        mode="set_to_match",
        title="Set vs Match",
        best_of=5,
        show_logistic_fit=False,
        highlight_player="Alice CHEN",
    )
    assert metrics.n_players == 2
    assert metrics.n_defenders == 1
    assert len(fig.data) >= 3


def test_win_rates_page_renders_and_filters_work() -> None:
    app_dir = project_root() / "app"
    page = AppTest.from_file(app_dir / "pages" / "3_Win_Rates.py").run(timeout=30)
    assert not page.exception

    # Verify all 3 per-plot Min Matches sliders exist with default 10
    assert len(page.slider) == 3
    for slider in page.slider:
        assert slider.value == 10

    # Toggle logistic S-curve on Plot 1 and switch Plot 2 to Best of 7
    page.toggle(key="pt_vs_set_logistic").set_value(True)
    page.slider(key="set_vs_match_min_matches").set_value(5)
    page.run(timeout=30)
    assert not page.exception

"""Unit tests for Glicko-2 upsets, prediction accuracy, and calibration helpers."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest
from app.lib.upsets import (
    attach_prior_match_counts,
    build_calibration_buckets,
    build_calibration_chart,
    build_upsets_table,
    compute_prediction_accuracy,
)

from tt_stats.analytics.export_app_data import default_output_dir


def _sample_ratings() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "match_sk": [1, 2, 3, 4],
            "event_sk": [10, 10, 11, 12],
            "gender_category": ["men", "men", "men", "women"],
            "rating_date": [
                date(2025, 1, 1),
                date(2025, 2, 1),
                date(2025, 3, 1),
                date(2025, 4, 1),
            ],
            "p1_player_sk": [100, 100, 300, 200],
            "p2_player_sk": [200, 300, 100, 400],
            "p1_pre_rating": [1800.0, 1820.0, 1600.0, 1500.0],
            "p1_pre_rd": [60.0, 60.0, 70.0, 350.0],
            "p1_pre_vol": [0.06, 0.06, 0.06, 0.06],
            "p1_post_rating": [1820.0, 1790.0, 1680.0, 1600.0],
            "p1_post_rd": [60.0, 60.0, 65.0, 220.0],
            "p1_post_vol": [0.06, 0.06, 0.06, 0.06],
            "p2_pre_rating": [1600.0, 1550.0, 1950.0, 1500.0],
            "p2_pre_rd": [70.0, 70.0, 55.0, 350.0],
            "p2_pre_vol": [0.06, 0.06, 0.06, 0.06],
            "p2_post_rating": [1580.0, 1590.0, 1890.0, 1400.0],
            "p2_post_rd": [70.0, 70.0, 55.0, 220.0],
            "p2_post_vol": [0.06, 0.06, 0.06, 0.06],
            # Match 1: p1 favored (0.75), p1 wins -> expected
            # Match 2: p1 favored (0.80), p2 wins -> upset! (loser prob = 80%)
            # Match 3: p2 favored (p2=0.88), p1 wins -> bigger upset! (loser = 88%)
            # Match 4: exact 50/50 tie -> excluded from accuracy & upsets
            "p1_expected_win_prob": [0.75, 0.80, 0.12, 0.50],
            "p1_actual_score": [1.0, 0.0, 1.0, 1.0],
        }
    )


def _sample_players() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "player_sk": [100, 200, 300, 400],
            "player_name": [
                "WANG Chuqin",
                "Hugo CALDERANO",
                "Anders LIND",
                "SUN Yingsha",
            ],
            "organization_code": ["CHN", "BRA", "DEN", "CHN"],
            "country_code": ["CHN", "BRA", "DEN", "CHN"],
        }
    )


def _sample_events() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "event_sk": [10, 11, 12],
            "event_name": ["Doha Open", "China Smash", "Macao Cup"],
        }
    )


def _sample_matches() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "match_sk": [1, 2, 3, 4],
            "round_code": ["QF", "R32", "R16", "F"],
            "games_won_p1": [3, 1, 3, 4],
            "games_won_p2": [1, 3, 2, 0],
        }
    )


def test_attach_prior_match_counts_and_accuracy() -> None:
    with_prior = attach_prior_match_counts(_sample_ratings()).sort("match_sk")
    assert with_prior["p1_prior_matches"].to_list() == [0, 1, 1, 0]
    assert with_prior["p2_prior_matches"].to_list() == [0, 0, 2, 0]

    acc_all = compute_prediction_accuracy(with_prior, min_prior_matches=0)
    assert acc_all["men"].decisive_matches == 3
    assert acc_all["men"].correct_predictions == 1
    assert acc_all["men"].upsets == 2
    assert acc_all["men"].accuracy_pct == pytest.approx(100.0 / 3.0)
    assert acc_all["women"].decisive_matches == 0

    acc_prior1 = compute_prediction_accuracy(with_prior, min_prior_matches=1)
    assert acc_prior1["men"].decisive_matches == 1
    assert acc_prior1["men"].upsets == 1


def test_build_upsets_table_ranking_and_perspective() -> None:
    with_prior = attach_prior_match_counts(_sample_ratings())
    upsets = build_upsets_table(
        with_prior,
        _sample_players(),
        _sample_events(),
        _sample_matches(),
        gender="men",
        min_prior_matches=0,
        rank_by="loser_prob",
    )
    assert upsets.height == 2
    assert upsets["match_sk"].to_list() == [3, 2]
    assert upsets["rank"].to_list() == [1, 2]

    biggest = upsets.row(0, named=True)
    assert biggest["winner_name"] == "Anders LIND"
    assert biggest["loser_name"] == "WANG Chuqin"
    assert biggest["event_name"] == "China Smash"
    assert biggest["score"] == "3-2"
    assert biggest["rating_diff"] == pytest.approx(350.0)
    assert biggest["loser_win_prob_pct"] == pytest.approx(88.0)
    assert biggest["winner_win_prob_pct"] == pytest.approx(12.0)
    assert biggest["winner_rating_delta"] == pytest.approx(80.0)
    assert biggest["winner_flag_url"] == "https://flagcdn.com/w40/dk.png"
    assert biggest["loser_flag_url"] == "https://flagcdn.com/w40/cn.png"

    second = upsets.row(1, named=True)
    # Match 2: p2 (Anders LIND) won 3-1, so score from winner view must be 3-1.
    assert second["score"] == "3-1"
    assert second["loser_win_prob_pct"] == pytest.approx(80.0)

    # Filter with min_prior_matches=1 leaves only match 3 (1 and 2 prior matches).
    upsets_est = build_upsets_table(
        with_prior,
        _sample_players(),
        _sample_events(),
        _sample_matches(),
        gender="men",
        min_prior_matches=1,
    )
    assert upsets_est["match_sk"].to_list() == [3]


def test_calibration_buckets_and_chart() -> None:
    with_prior = attach_prior_match_counts(_sample_ratings())
    buckets = build_calibration_buckets(with_prior, gender="men", min_prior_matches=0)
    assert buckets["prob_bucket"].to_list() == [75, 80, 88]
    assert buckets["matches"].to_list() == [1, 1, 1]
    fig = build_calibration_chart(buckets, gender_label="Men")
    assert len(fig.data) == 2


def test_upsets_and_accuracy_smoke_on_real_parquet() -> None:
    data_dir = default_output_dir()
    ratings = pl.read_parquet(data_dir / "fct_glicko_match_ratings.parquet")
    players = pl.read_parquet(data_dir / "dim_players.parquet")
    events = pl.read_parquet(data_dir / "dim_events.parquet")
    matches = pl.read_parquet(data_dir / "fct_senior_singles_matches.parquet")

    with_prior = attach_prior_match_counts(ratings)
    acc = compute_prediction_accuracy(with_prior, min_prior_matches=10)
    for gender in ("men", "women"):
        m = acc[gender]
        assert m.decisive_matches > 5000
        assert m.accuracy_pct is not None
        assert 65.0 < m.accuracy_pct < 95.0

    men_upsets = build_upsets_table(
        with_prior,
        players,
        events,
        matches,
        gender="men",
        min_prior_matches=10,
    )
    assert men_upsets.height > 100
    assert men_upsets["loser_win_prob_pct"].is_sorted(descending=True)

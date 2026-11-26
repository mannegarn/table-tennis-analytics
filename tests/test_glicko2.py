"""Unit tests for the Glicko-2 rating engine."""

from __future__ import annotations

from datetime import date

import pytest

from tt_stats.analytics.glicko2 import (
    Glicko2Config,
    MatchInput,
    PlayerState,
    apply_inactivity_decay,
    compute_glicko2_ratings,
    expected_win_probability,
    update_player_state,
)


def test_glickman_paper_worked_example() -> None:
    """Verify against Mark Glickman's published Glicko-2 worked example.

    Player at (1500, RD=200, vol=0.06) with tau=0.5 plays three opponents:
    - (1400, RD=30): win (1.0)
    - (1550, RD=100): loss (0.0)
    - (1700, RD=300): loss (0.0)

    Expected updated values from Glickman's paper:
    - rating ≈ 1464.06
    - RD ≈ 151.52
    - volatility ≈ 0.05999
    """
    config = Glicko2Config(tau=0.5)
    player = PlayerState(rating=1500.0, rd=200.0, vol=0.06)
    opponents = [
        (PlayerState(rating=1400.0, rd=30.0, vol=0.06), 1.0),
        (PlayerState(rating=1550.0, rd=100.0, vol=0.06), 0.0),
        (PlayerState(rating=1700.0, rd=300.0, vol=0.06), 0.0),
    ]

    updated = update_player_state(player, opponents, config)

    assert updated.rating == pytest.approx(1464.06, abs=0.02)
    assert updated.rd == pytest.approx(151.52, abs=0.02)
    assert updated.vol == pytest.approx(0.05999, abs=1e-4)


def test_equal_players_win_and_draw_symmetry() -> None:
    """Symmetric initial players produce mirror rating changes on win and 0 on draw."""
    config = Glicko2Config()
    matches = [
        MatchInput(
            match_sk=1,
            event_sk=10,
            gender_category="men",
            rating_date=date(2025, 1, 1),
            p1_player_sk=101,
            p2_player_sk=102,
            winner_side=1,
        ),
        MatchInput(
            match_sk=2,
            event_sk=10,
            gender_category="men",
            rating_date=date(2025, 1, 2),
            p1_player_sk=201,
            p2_player_sk=202,
            winner_side=None,  # draw
        ),
    ]

    rows, players_rated = compute_glicko2_ratings(matches, config)

    assert players_rated == 4
    assert len(rows) == 2

    win_row = rows[0]
    assert win_row.p1_expected_win_prob == pytest.approx(0.5)
    assert win_row.p1_actual_score == 1.0
    assert win_row.p1_post_rating > 1500.0
    assert win_row.p2_post_rating < 1500.0
    assert (win_row.p1_post_rating - 1500.0) == pytest.approx(
        1500.0 - win_row.p2_post_rating
    )
    assert win_row.p1_post_rd == pytest.approx(win_row.p2_post_rd)

    draw_row = rows[1]
    assert draw_row.p1_actual_score == 0.5
    assert draw_row.p1_post_rating == pytest.approx(1500.0)
    assert draw_row.p2_post_rating == pytest.approx(1500.0)
    assert draw_row.p1_post_rd < 350.0


def test_zero_decay_by_default_and_optional_decay() -> None:
    """Default config applies zero RD decay; setting rd_decay_days increases RD."""
    state = PlayerState(
        rating=1800.0,
        rd=80.0,
        vol=0.06,
        last_date=date(2024, 1, 1),
    )
    later = date(2025, 1, 1)

    no_decay = apply_inactivity_decay(state, later, Glicko2Config(rd_decay_days=None))
    assert no_decay.rd == pytest.approx(80.0)

    with_decay = apply_inactivity_decay(state, later, Glicko2Config(rd_decay_days=30.0))
    assert with_decay.rd > 80.0
    assert with_decay.rd <= 350.0


def test_event_mode_uses_pre_event_ratings_for_all_matches_in_event() -> None:
    """In event mode, all matches in the same event see pre-event ratings."""
    config = Glicko2Config(update_mode="event")
    matches = [
        MatchInput(
            match_sk=1,
            event_sk=500,
            gender_category="women",
            rating_date=date(2025, 3, 1),
            p1_player_sk=1,
            p2_player_sk=2,
            winner_side=1,
        ),
        MatchInput(
            match_sk=2,
            event_sk=500,
            gender_category="women",
            rating_date=date(2025, 3, 2),
            p1_player_sk=1,
            p2_player_sk=3,
            winner_side=1,
        ),
    ]

    rows, _ = compute_glicko2_ratings(matches, config)
    assert rows[0].p1_pre_rating == pytest.approx(1500.0)
    assert rows[1].p1_pre_rating == pytest.approx(1500.0)
    assert rows[0].p1_post_rating == pytest.approx(rows[1].p1_post_rating)


def test_expected_win_probability_monotonic() -> None:
    """Higher-rated player always has >50% expected win probability."""
    strong = PlayerState(rating=1900.0, rd=60.0, vol=0.06)
    weak = PlayerState(rating=1500.0, rd=60.0, vol=0.06)

    prob = expected_win_probability(strong, weak)
    assert 0.5 < prob < 1.0
    assert expected_win_probability(weak, strong) == pytest.approx(1.0 - prob)


def test_config_validation() -> None:
    """Invalid Glicko-2 config values raise ValueError."""
    with pytest.raises(ValueError, match="initial_rd"):
        Glicko2Config(initial_rd=0.0)
    with pytest.raises(ValueError, match="tau"):
        Glicko2Config(tau=-0.1)
    with pytest.raises(ValueError, match="rd_decay_days"):
        Glicko2Config(rd_decay_days=0.0)

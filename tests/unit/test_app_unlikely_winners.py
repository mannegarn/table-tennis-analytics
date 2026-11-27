"""Unit tests for the Unlikely Winners (tournament-level Glicko-2) analytics."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest
from app.lib.unlikely_winners import (
    TOURNAMENT_TYPE_OPTIONS,
    build_unlikely_winners_chart,
    build_unlikely_winners_table,
    format_win_prob_pct,
    map_tournament_type,
    top_unlikely_winners_by_gender,
)
from app.lib.upsets import attach_prior_match_counts

from tt_stats.analytics.export_app_data import default_output_dir


def _sample_events() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "event_sk": [10, 20, 30],
            "event_name": [
                "Olympic Games",
                "World Team Championships",
                "WTT Feeder Open",
            ],
            "event_year": [2024, 2024, 2025],
            "event_type": ["Olympic Games", "WTTC", "WTT Feeder"],
            "start_date": [date(2024, 8, 1), date(2024, 2, 16), date(2025, 3, 1)],
        }
    )


def _sample_players() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "player_sk": [1, 2, 3, 4, 5, 6],
            "player_name": [
                "Champion One",
                "Runner Up",
                "Semi Loser",
                "Qual Loser",
                "Group Opponent",
                "Women Champ",
            ],
            "organization_code": ["DEN", "CHN", "JPN", "FRA", "GER", "SWE"],
            "country_code": ["DEN", "CHN", "JPN", "FRA", "GER", "SWE"],
            "head_shot": [
                "https://example.com/p1.png",
                None,
                None,
                None,
                None,
                None,
            ],
            "gender": ["M", "M", "M", "M", "M", "F"],
        }
    )


def _sample_matches() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "match_sk": [101, 102, 103, 104, 105, 106, 201, 301],
            "event_sk": [10, 10, 10, 10, 10, 10, 20, 30],
            "is_team_rubber": [False, False, False, False, False, True, True, False],
            "p1_player_sk": [1, 1, 1, 1, 1, 2, 3, 6],
            "p2_player_sk": [5, 4, 3, 2, 5, 1, 2, 4],
            "winner_player_sk": [5, 1, 1, 1, 1, 2, 3, 6],
            "loser_player_sk": [1, 4, 3, 2, 5, 1, 2, 4],
            "gender_category": [
                "men",
                "men",
                "men",
                "men",
                "men",
                "men",
                "men",
                "women",
            ],
            "round_code": ["GROUP", "QUAL", "SF", "F", "QF", "F", "F", "F"],
            "round_stage": [
                "group",
                "qualifying",
                "knockout",
                "knockout",
                "knockout",
                "knockout",
                "knockout",
                "knockout",
            ],
            "round_order": [1, 2, 5, 6, 4, 6, 6, 6],
            "start_date_utc": [
                date(2024, 8, 1),
                date(2024, 8, 2),
                date(2024, 8, 3),
                date(2024, 8, 4),
                date(2024, 8, 3),
                date(2024, 8, 10),
                date(2024, 2, 25),
                date(2025, 3, 5),
            ],
            "result_type": [
                "played",
                "played",
                "played",
                "played",
                "walkover",
                "played",
                "played",
                "played",
            ],
            "games_won_p1": [1, 3, 4, 2, None, 3, 3, 4],
            "games_won_p2": [3, 1, 2, 4, None, 0, 1, 1],
        }
    )


def _sample_ratings() -> pl.DataFrame:
    # Note: match 105 (QF walkover) is unrated and intentionally absent.
    # Event 10 Men's Champ (player 1):
    # - 101 GROUP loss (excluded!)
    # - 102 QUAL win: p1=1 -> prob 0.40
    # - 103 SF win: p1=1 -> prob 0.25
    # - 105 QF walkover win: unrated -> 1.0
    # - 104 F win: p1=1 -> prob 0.50
    # Expected product = 0.40 * 0.25 * 0.50 = 0.05 -> 5.0%
    return pl.DataFrame(
        {
            "match_sk": [101, 102, 103, 104, 106, 201, 301],
            "event_sk": [10, 10, 10, 10, 10, 20, 30],
            "gender_category": [
                "men",
                "men",
                "men",
                "men",
                "men",
                "men",
                "women",
            ],
            "rating_date": [
                date(2024, 8, 1),
                date(2024, 8, 2),
                date(2024, 8, 3),
                date(2024, 8, 4),
                date(2024, 8, 10),
                date(2024, 2, 25),
                date(2025, 3, 5),
            ],
            "p1_player_sk": [1, 1, 1, 1, 2, 3, 6],
            "p2_player_sk": [5, 4, 3, 2, 1, 2, 4],
            "p1_pre_rating": [1600.0] * 7,
            "p1_pre_rd": [60.0] * 7,
            "p1_pre_vol": [0.06] * 7,
            "p1_post_rating": [1610.0] * 7,
            "p1_post_rd": [60.0] * 7,
            "p1_post_vol": [0.06] * 7,
            "p2_pre_rating": [1800.0] * 7,
            "p2_pre_rd": [60.0] * 7,
            "p2_pre_vol": [0.06] * 7,
            "p2_post_rating": [1790.0] * 7,
            "p2_post_rd": [60.0] * 7,
            "p2_post_vol": [0.06] * 7,
            "p1_expected_win_prob": [0.30, 0.40, 0.25, 0.50, 0.80, 0.60, 0.70],
            "p1_actual_score": [0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        }
    )


def test_unlikely_winners_excludes_team_and_group_and_multiplies_path() -> None:
    matches = _sample_matches().with_columns(
        # Ensure Final 104 has p1=1 winning 4-2, and test p2 winning in match 301.
        pl.when(pl.col("match_sk") == 104)
        .then(4)
        .otherwise(pl.col("games_won_p1"))
        .alias("games_won_p1"),
        pl.when(pl.col("match_sk") == 104)
        .then(2)
        .otherwise(pl.col("games_won_p2"))
        .alias("games_won_p2"),
    )
    ratings = attach_prior_match_counts(_sample_ratings())
    table = build_unlikely_winners_table(
        matches,
        ratings,
        _sample_players(),
        _sample_events(),
        min_prior_matches=0,
    )

    # Event 20 (pure team event) and Event 10's team Final (106) must be excluded.
    assert table.height == 2
    assert table["event_sk"].to_list() == [10, 30]

    olympics = table.row(0, named=True)
    assert olympics["winner_name"] == "Champion One"
    assert olympics["loser_name"] == "Runner Up"
    assert olympics["final_score"] == "4-2"
    assert olympics["tournament_type"] == "Olympics"
    assert olympics["winner_flag_url"] == "https://flagcdn.com/w40/dk.png"
    assert olympics["loser_flag_url"] == "https://flagcdn.com/w40/cn.png"
    assert olympics["winner_photo_url"] == "https://example.com/p1.png"
    # 102 (QUAL), 103 (SF), 105 (QF WO), 104 (F) -> 4 wins (3 rated); group excluded.
    assert olympics["wins_count"] == 4
    assert olympics["rated_wins"] == 3
    assert olympics["tournament_win_prob_pct"] == pytest.approx(5.0)
    assert olympics["round_path"] == "QUAL → F"

    women_top = top_unlikely_winners_by_gender(table, gender="women", limit=10)
    assert women_top.height == 1
    assert women_top["winner_name"].to_list() == ["Women Champ"]
    assert women_top["tournament_win_prob_pct"].to_list() == pytest.approx([70.0])

    fig = build_unlikely_winners_chart(table, log_scale=True)
    assert len(fig.data) == 2


def test_map_tournament_type_and_filter() -> None:
    assert map_tournament_type("Olympic Games") == "Olympics"
    assert map_tournament_type("Singles World Cup") == "World Cup"
    assert map_tournament_type("WTTC") == "World Championships (WTTC)"
    assert map_tournament_type("WTT Grand Smash") == "WTT Grand Smash"
    assert map_tournament_type("WTT Finals") == "WTT Finals"
    assert map_tournament_type("WTT Cup Finals") == "WTT Finals"
    assert map_tournament_type("WTT Champions") == "WTT Champions"
    assert map_tournament_type("WTT Star Contender") == "WTT Star Contender"
    assert map_tournament_type("WTT Contender") == "WTT Contender"
    assert map_tournament_type("WTT Feeder") == "WTT Feeder"
    assert map_tournament_type("Continental Championships") == "Continental"
    assert map_tournament_type("Continental Cups") == "Continental"
    assert map_tournament_type("Continental") == "Continental"
    assert map_tournament_type("WTT Exhibition") == "Other / Open"
    assert map_tournament_type(None) == "Other / Open"

    ratings = attach_prior_match_counts(_sample_ratings())
    olympics_only = build_unlikely_winners_table(
        _sample_matches(),
        ratings,
        _sample_players(),
        _sample_events(),
        min_prior_matches=0,
        tournament_types=["Olympics"],
    )
    assert olympics_only["event_sk"].to_list() == [10]

    feeder_only = build_unlikely_winners_table(
        _sample_matches(),
        ratings,
        _sample_players(),
        _sample_events(),
        min_prior_matches=0,
        tournament_types=["WTT Feeder"],
    )
    assert feeder_only["event_sk"].to_list() == [30]


def test_min_prior_matches_filters_entering_tournament() -> None:
    ratings = attach_prior_match_counts(_sample_ratings())
    # Player 1 enters 102 with 1 prior match (from 101); Player 6 enters 301 with 0.
    filtered = build_unlikely_winners_table(
        _sample_matches(),
        ratings,
        _sample_players(),
        _sample_events(),
        min_prior_matches=1,
    )
    assert filtered["event_sk"].to_list() == [10]

    none_left = build_unlikely_winners_table(
        _sample_matches(),
        ratings,
        _sample_players(),
        _sample_events(),
        min_prior_matches=2,
    )
    assert none_left.is_empty()


def test_format_win_prob_pct() -> None:
    assert format_win_prob_pct(0.004) == "<0.01%"
    assert format_win_prob_pct(0.0458) == "0.05%"
    assert format_win_prob_pct(3.456) == "3.46%"
    assert format_win_prob_pct(24.78) == "24.8%"


def test_unlikely_winners_smoke_on_real_parquet() -> None:
    data_dir = default_output_dir()
    matches = pl.read_parquet(data_dir / "fct_senior_singles_matches.parquet")
    ratings = attach_prior_match_counts(
        pl.read_parquet(data_dir / "fct_glicko_match_ratings.parquet")
    )
    players = pl.read_parquet(data_dir / "dim_players.parquet")
    events = pl.read_parquet(data_dir / "dim_events.parquet")

    df = build_unlikely_winners_table(
        matches,
        ratings,
        players,
        events,
        min_prior_matches=10,
    )
    assert df.height > 200
    assert df["tournament_win_prob_pct"].is_sorted(descending=False)
    assert (df["tournament_win_prob_pct"] > 0.0).all()
    assert (df["tournament_win_prob_pct"] <= 100.0).all()
    assert set(df["tournament_type"].unique().to_list()) <= set(TOURNAMENT_TYPE_OPTIONS)

    for required_type in ("Olympics", "World Cup", "WTT Grand Smash", "WTT Finals"):
        sub = build_unlikely_winners_table(
            matches,
            ratings,
            players,
            events,
            min_prior_matches=10,
            tournament_types=[required_type],
        )
        assert sub.height >= 2
        assert sub["tournament_type"].unique().to_list() == [required_type]

    # Pure team events must be absent.
    team_names = df.filter(
        pl.col("event_name").str.to_lowercase().str.contains("world team|mixed team")
    )
    assert team_names.is_empty()

    men_top10 = top_unlikely_winners_by_gender(df, gender="men", limit=10)
    women_top10 = top_unlikely_winners_by_gender(df, gender="women", limit=10)
    assert men_top10.height == 10
    assert women_top10.height == 10

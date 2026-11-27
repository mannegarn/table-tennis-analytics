"""Unit tests for the Streamlit summary calculations and page smoke tests."""

from __future__ import annotations

from datetime import date

import polars as pl
from app.lib.data import (
    build_yearly_matches_table,
    compute_home_insights,
    compute_summary_stats,
)
from streamlit.testing.v1 import AppTest

from tt_stats.core.config import project_root


def test_compute_summary_stats() -> None:
    events = pl.DataFrame(
        {
            "event_sk": [1, 2, 3],
            "event_name": ["Empty Old Event", "First Active Open", "Latest Tour"],
            "event_year": [1990, 2021, 2026],
            "start_date": [date(1990, 1, 1), date(2021, 3, 10), date(2026, 11, 1)],
            "end_date": [date(1990, 1, 5), date(2021, 3, 14), date(2026, 11, 5)],
            "country": ["SWE", "QAT", "GER"],
        }
    )
    players = pl.DataFrame(
        {
            "player_sk": [10, 20, 30, 40],
            "gender": ["M", "F", "M", "F"],
            "country_code": ["CHN", None, "GER", "BRA"],
            "organization_code": ["CHN", "JPN", "GER", "BRA"],
            "age": [36, 19, None, 51],
        }
    )
    senior_matches = pl.DataFrame(
        {
            "match_sk": [100, 101, 102],
            "event_sk": [2, 2, 3],
            "p1_player_sk": [10, 20, 10],
            "p2_player_sk": [30, 20, 30],
            "gender_category": ["men", "women", "men"],
            "best_of": [5, 7, 5],
        }
    )
    catalog_rollup = pl.DataFrame(
        {
            "event_sk": [2, 3],
            "gender_category": ["men", "women"],
            "discipline": ["singles", "doubles"],
            "is_youth": [False, False],
            "is_para": [False, False],
            "match_count": [10, 25],
        }
    )

    stats = compute_summary_stats(
        events,
        players,
        senior_matches,
        catalog_rollup,
    )

    assert stats.total_events == 3
    assert stats.events_with_senior_singles == 2
    assert stats.events_with_catalog_matches == 2
    assert stats.catalog_first_event_date == date(1990, 1, 1)
    assert stats.catalog_last_event_date == date(2026, 11, 1)

    assert stats.senior_total_matches == 3
    assert stats.senior_mens_matches == 2
    assert stats.senior_womens_matches == 1
    assert stats.senior_bo5_matches == 2
    assert stats.senior_bo7_matches == 1
    assert stats.senior_other_format_matches == 0
    assert stats.catalog_total_matches == 35

    assert stats.total_players == 3
    assert stats.mens_players == 2
    assert stats.womens_players == 1
    assert stats.distinct_countries == 3
    assert stats.players_with_age == 2
    assert stats.min_age == 19
    assert stats.max_age == 36

    assert stats.first_senior_event is not None
    assert stats.first_senior_event.event_name == "First Active Open"
    assert stats.first_senior_event.match_count == 2

    assert stats.last_senior_event is not None
    assert stats.last_senior_event.event_name == "Latest Tour"
    assert stats.last_senior_event.match_count == 1


def test_build_yearly_matches_table() -> None:
    events = pl.DataFrame(
        {
            "event_sk": [1, 2],
            "event_year": [2024, 2025],
        }
    )
    senior_matches = pl.DataFrame(
        {
            "event_sk": [1, 1, 2],
            "gender_category": ["men", "women", "men"],
        }
    )
    yearly = build_yearly_matches_table(events, senior_matches)
    assert yearly.height == 3
    assert yearly.filter(pl.col("event_year") == 2024)["Matches"].sum() == 2


def test_streamlit_pages_render_without_error() -> None:
    app_dir = project_root() / "app"
    home = AppTest.from_file(app_dir / "Home.py").run(timeout=30)
    assert not home.exception

    summary = AppTest.from_file(app_dir / "pages" / "2_Data_Overview.py").run(
        timeout=30
    )
    assert not summary.exception

    about = AppTest.from_file(app_dir / "pages" / "1_About.py").run(timeout=30)
    assert not about.exception

    win_rates = AppTest.from_file(app_dir / "pages" / "3_Win_Rates.py").run(timeout=30)
    assert not win_rates.exception

    serve_receive = AppTest.from_file(app_dir / "pages" / "4_Service_Win_Rate.py").run(
        timeout=30
    )
    assert not serve_receive.exception

    glicko = AppTest.from_file(app_dir / "pages" / "5_Glicko_Ratings.py").run(
        timeout=30
    )
    assert not glicko.exception

    upsets = AppTest.from_file(app_dir / "pages" / "6_Upsets.py").run(timeout=30)
    assert not upsets.exception

    table_types = AppTest.from_file(app_dir / "pages" / "7_Table_Types.py").run(
        timeout=30
    )
    assert not table_types.exception


def test_compute_home_insights() -> None:
    events = pl.DataFrame(
        {
            "event_sk": [1],
            "event_name": ["Test Open"],
            "event_year": [2025],
            "start_date": [date(2025, 5, 1)],
            "end_date": [date(2025, 5, 4)],
            "country": ["GER"],
        }
    )
    players = pl.DataFrame(
        {
            "player_sk": [10, 20],
            "player_name": ["Alice CHEN", "Bob MULLER"],
            "organization_code": ["CHN", "GER"],
            "country_code": ["CHN", "GER"],
            "gender": ["F", "M"],
            "style": ["Defender", "Attacker"],
            "grip": ["Shakehand", "Shakehand"],
            "hand": ["Right", "Right"],
            "age": [24, 27],
        }
    )
    senior_matches = pl.DataFrame(
        {
            "match_sk": [100],
            "event_sk": [1],
            "p1_player_sk": [10],
            "p2_player_sk": [20],
            "winner_side": [1],
            "gender_category": ["women"],
            "best_of": [5],
            "result_type": ["played"],
            "is_default_result": [False],
            "has_result": [True],
            "points_won_p1": [45],
            "points_won_p2": [55],
        }
    )
    senior_games = pl.DataFrame(
        {
            "match_sk": [100, 100, 100, 100, 100],
            "p1_points": [11, 11, 2, 2, 19],
            "p2_points": [9, 9, 11, 11, 15],
            "winner_side": [1, 1, 2, 2, 1],
            "is_valid_game_score": [True, True, True, True, True],
        }
    )
    catalog_rollup = pl.DataFrame(
        {
            "event_sk": [1],
            "gender_category": ["women"],
            "discipline": ["singles"],
            "is_youth": [False],
            "is_para": [False],
            "match_count": [1],
        }
    )
    sr_raw = pl.DataFrame(
        {
            "player_sk": [10, 20],
            "Player": ["Alice CHEN", "Bob MULLER"],
            "Organization": ["CHN", "GER"],
            "Gender": ["F", "M"],
            "Style": ["Defender", "Attacker"],
            "Grip": ["Shakehand", "Shakehand"],
            "Hand": ["Right", "Right"],
            "Matches": [10, 10],
            "Points Played": [100, 100],
            "Points Won": [52, 48],
            "Points Lost": [48, 52],
            "Serve Points": [50, 50],
            "Serve Won": [28, 26],
            "Serve Lost": [22, 24],
            "Receive Points": [50, 50],
            "Receive Won": [24, 22],
            "Receive Lost": [26, 28],
        }
    )

    insights = compute_home_insights(
        events=events,
        players=players,
        senior_matches=senior_matches,
        senior_games=senior_games,
        catalog_rollup=catalog_rollup,
        serve_receive_raw=sr_raw,
    )
    assert insights.senior_total_matches == 1
    assert insights.senior_year_min == 2025
    assert insights.senior_year_max == 2025
    assert insights.commentary_points == 100
    assert insights.global_serve_win_pct == 54.0
    assert insights.global_receive_win_pct == 46.0
    assert insights.server_edge_pp == 8.0
    assert insights.bo5_matches == 1
    assert insights.bo5_won_below_50 == 1
    assert insights.bo5_won_below_50_pct == 100.0

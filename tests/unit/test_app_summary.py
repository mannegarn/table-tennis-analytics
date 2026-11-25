"""Unit tests for the Streamlit summary calculations and page smoke tests."""

from __future__ import annotations

from datetime import date

import polars as pl
from app.lib.data import build_yearly_matches_table, compute_summary_stats
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

"""Unit tests for pure-Polars Glicko-2 leaderboard, history, H2H, and asset helpers."""

from __future__ import annotations

import base64
from datetime import date
from pathlib import Path
from typing import Literal

import polars as pl
import pytest
from app.lib.glicko import (
    IOC_TO_CONTINENT,
    IOC_TO_ISO2,
    NEUTRAL_ORG_CODES,
    build_as_of_event_options,
    build_glicko_leaderboard,
    build_head_to_head,
    build_player_rating_history,
    continent_for_code,
    flag_url_for_code,
    glicko2_win_probability,
    resolve_placeholder_data_uri,
    sanitize_headshot_url,
)

from tt_stats.analytics.export_app_data import default_output_dir


def _sample_players() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "player_sk": [1, 2, 3],
            "player_name": ["Alice CHEN", "Bob MULLER", "Cara SATO"],
            "organization_code": ["CHN", "GER", "JPN"],
            "country_code": ["CHN", "GER", "JPN"],
            "organization_name": ["China", "Germany", "Japan"],
            "country_name": ["China", "Germany", "Japan"],
            "style": ["Attacker", None, "Defender"],
            "grip": ["Shakehand", "", "Shakehand"],
            "hand": ["Left", "Right", None],
            "age": [24, 28, 22],
            "head_shot": [
                "https://example.com/photos/1_Alice CHEN.png",
                None,
                "",
            ],
            "gender": ["F", "M", "F"],
        }
    )


def _sample_ratings() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "match_sk": [101, 102, 103],
            "event_sk": [10, 11, 12],
            "gender_category": ["women", "women", "women"],
            "rating_date": [
                date(2025, 1, 10),
                date(2025, 6, 1),
                date(2025, 8, 1),
            ],
            "p1_player_sk": [1, 1, 3],
            "p2_player_sk": [3, 3, 1],
            "p1_pre_rating": [1500.0, 1650.0, 1400.0],
            "p1_pre_rd": [350.0, 220.0, 180.0],
            "p1_pre_vol": [0.06, 0.06, 0.06],
            "p1_post_rating": [1650.0, 1780.0, 1360.0],
            "p1_post_rd": [220.0, 140.0, 130.0],
            "p1_post_vol": [0.06, 0.06, 0.06],
            "p2_pre_rating": [1500.0, 1350.0, 1780.0],
            "p2_pre_rd": [350.0, 220.0, 140.0],
            "p2_pre_vol": [0.06, 0.06, 0.06],
            "p2_post_rating": [1350.0, 1400.0, 1740.0],
            "p2_post_rd": [220.0, 180.0, 110.0],
            "p2_post_vol": [0.06, 0.06, 0.06],
            "p1_expected_win_prob": [0.50, 0.78, 0.15],
            "p1_actual_score": [1.0, 0.5, 0.0],
        }
    )


def test_all_dim_players_org_codes_have_flag_mapping_or_are_neutral() -> None:
    players_path = default_output_dir() / "dim_players.parquet"
    assert players_path.exists()
    players = pl.read_parquet(players_path)

    codes: set[str] = set()
    for col in ("organization_code", "country_code"):
        codes.update(
            c.strip().upper()
            for c in players[col].drop_nulls().to_list()
            if c and c.strip()
        )

    unmapped = sorted(
        c for c in codes if c not in IOC_TO_ISO2 and c not in NEUTRAL_ORG_CODES
    )
    assert unmapped == []
    assert flag_url_for_code("CHN") == "https://flagcdn.com/w40/cn.png"
    assert flag_url_for_code("ENG") == "https://flagcdn.com/w40/gb-eng.png"
    assert flag_url_for_code("REF") is None
    assert flag_url_for_code(None) is None


def test_sanitize_headshot_url_and_placeholder_fallback(tmp_path: Path) -> None:
    default_svg = tmp_path / "player_default.svg"
    default_svg.write_text("<svg>default</svg>", encoding="utf-8")
    men_png = tmp_path / "player_men.png"
    men_png.write_bytes(b"png-men-bytes")

    encoded = sanitize_headshot_url(
        "https://wttsimfiles.blob.core.windows.net/wtt-media/photos/"
        "400px/100089_Headshot_R_Luke ABRAHAMS.png",
        placeholders_dir=tmp_path,
    )
    assert encoded == (
        "https://wttsimfiles.blob.core.windows.net/wtt-media/photos/400px/"
        "100089_Headshot_R_Luke%20ABRAHAMS.png"
    )

    men_uri = resolve_placeholder_data_uri("M", placeholders_dir=tmp_path)
    assert men_uri == "data:image/png;base64," + base64.b64encode(
        b"png-men-bytes"
    ).decode("ascii")

    women_uri = sanitize_headshot_url(None, gender="F", placeholders_dir=tmp_path)
    assert women_uri.startswith("data:image/svg+xml;base64,")
    assert base64.b64decode(women_uri.split(",", 1)[1]) == b"<svg>default</svg>"


def test_real_theme_aware_placeholders_resolve_for_gender_and_theme() -> None:
    men_dark = resolve_placeholder_data_uri("M", theme="dark")
    men_light = resolve_placeholder_data_uri("M", theme="light")
    women_dark = resolve_placeholder_data_uri("F", theme="dark")
    women_light = resolve_placeholder_data_uri("F", theme="light")

    for uri in (men_dark, men_light, women_dark, women_light):
        assert uri.startswith("data:image/png;base64,")
    assert len({men_dark, men_light, women_dark, women_light}) == 4


def test_build_glicko_leaderboard_stats_filters_and_time_travel() -> None:
    players = _sample_players()
    ratings = _sample_ratings()

    lb = build_glicko_leaderboard(
        ratings,
        players,
        gender="women",
        trend_days=90,
    )
    assert lb.height == 2
    assert lb["rank"].to_list() == [1, 2]
    assert lb["player_sk"].to_list() == [1, 3]

    alice = lb.row(0, named=True)
    assert alice["rating"] == pytest.approx(1740.0)
    assert alice["peak_rating"] == pytest.approx(1780.0)
    assert alice["peak_date"] == date(2025, 6, 1)
    assert alice["peak_display"] == "1780.0 (01/06/25)"
    assert alice["country_name"] == "China"
    assert alice["avg_rating"] == pytest.approx((1650.0 + 1780.0 + 1740.0) / 3.0)
    assert alice["matches"] == 3
    assert alice["wins"] == 2
    assert alice["losses"] == 0
    assert alice["draws"] == 1
    assert alice["win_pct"] == pytest.approx(250.0 / 3.0)
    # Anchor is 2025-08-01; cutoff 90d prior is 2025-05-03 -> baseline is 1650.
    assert alice["trend_delta"] == pytest.approx(1740.0 - 1650.0)
    assert alice["flag_url"] == "https://flagcdn.com/w40/cn.png"
    assert alice["photo_url"] == "https://example.com/photos/1_Alice%20CHEN.png"

    cara = lb.row(1, named=True)
    assert cara["hand"] == "Unknown"
    assert cara["photo_url"].startswith("data:image/")

    # Time-travel back to June 15 excludes the August match.
    lb_june = build_glicko_leaderboard(
        ratings,
        players,
        gender="women",
        as_of_date=date(2025, 6, 15),
    )
    alice_june = lb_june.filter(pl.col("player_sk") == 1).row(0, named=True)
    assert alice_june["rating"] == pytest.approx(1780.0)
    assert alice_june["matches"] == 2

    # max_rd filter removes players above threshold.
    lb_tight_rd = build_glicko_leaderboard(
        ratings,
        players,
        gender="women",
        max_rd=120.0,
    )
    assert lb_tight_rd["player_sk"].to_list() == [1]


def test_build_player_rating_history() -> None:
    players = _sample_players()
    ratings = _sample_ratings()
    events = pl.DataFrame(
        {
            "event_sk": [10, 11, 12],
            "event_name": ["Doha Open", "Singapore Smash", "Yokohama Cup"],
        }
    )
    matches = pl.DataFrame(
        {
            "match_sk": [101, 102, 103],
            "round_code": ["QF", "SF", "F"],
            "games_won_p1": [3, 2, 1],
            "games_won_p2": [1, 2, 3],
        }
    )

    hist = build_player_rating_history(
        ratings,
        players,
        events,
        matches,
        player_sks=[1],
    )
    assert hist.height == 3
    assert hist["event_name"].to_list() == [
        "Doha Open",
        "Singapore Smash",
        "Yokohama Cup",
    ]
    assert hist["result"].to_list() == ["W", "D", "W"]
    # Match 103 has player 1 on p2 side (games_won_p1=1, games_won_p2=3) -> 3-1.
    assert hist["score"].to_list() == ["3-1", "2-2", "3-1"]
    assert hist["opponent_name"].to_list() == ["Cara SATO", "Cara SATO", "Cara SATO"]


def test_build_head_to_head() -> None:
    players = _sample_players()
    ratings = _sample_ratings()
    events = pl.DataFrame(
        {
            "event_sk": [10, 11, 12],
            "event_name": ["Doha Open", "Singapore Smash", "Yokohama Cup"],
        }
    )
    matches = pl.DataFrame(
        {
            "match_sk": [101, 102, 103],
            "round_code": ["QF", "SF", "F"],
            "games_won_p1": [3, 2, 1],
            "games_won_p2": [1, 2, 3],
        }
    )

    h2h = build_head_to_head(ratings, players, events, matches, 1, 3)
    assert h2h.p1_name == "Alice CHEN"
    assert h2h.p2_name == "Cara SATO"
    assert h2h.p1_rating == pytest.approx(1740.0)
    assert h2h.p2_rating == pytest.approx(1360.0)
    assert h2h.rating_diff == pytest.approx(380.0)
    assert h2h.p1_win_prob is not None
    assert h2h.p2_win_prob is not None
    assert h2h.p1_win_prob > 0.5
    assert h2h.p1_win_prob + h2h.p2_win_prob == pytest.approx(1.0)
    assert h2h.p1_win_prob == pytest.approx(
        glicko2_win_probability(1740.0, 110.0, 1360.0, 130.0)
    )
    assert h2h.total_matches == 3
    assert h2h.p1_wins == 2
    assert h2h.p2_wins == 0
    assert h2h.draws == 1
    assert h2h.matches_df["score"].to_list() == ["3-1", "2-2", "3-1"]


def test_build_glicko_leaderboard_smoke_on_real_parquet() -> None:
    data_dir = default_output_dir()
    ratings = pl.read_parquet(data_dir / "fct_glicko_match_ratings.parquet")
    players = pl.read_parquet(data_dir / "dim_players.parquet")

    genders: tuple[Literal["men", "women"], ...] = ("men", "women")
    for gender in genders:
        lb = build_glicko_leaderboard(
            ratings,
            players,
            gender=gender,
            max_rd=150.0,
            min_matches=5,
        )
        assert lb.height > 100
        assert lb["rank"].to_list()[:3] == [1, 2, 3]
        assert lb["rating"].is_sorted(descending=True)


def test_build_as_of_event_options() -> None:
    ratings = _sample_ratings()
    events = pl.DataFrame(
        {
            "event_sk": [10, 11, 12],
            "event_name": ["Doha Open", "Singapore Smash", "Yokohama Cup"],
        }
    )

    options = build_as_of_event_options(ratings, events, gender="women")
    assert options[0] == ("Latest (01/08/25 — All matches)", None)
    assert options[1] == ("01/08/25 · Yokohama Cup", date(2025, 8, 1))
    assert options[2] == ("01/06/25 · Singapore Smash", date(2025, 6, 1))
    assert options[3] == ("10/01/25 · Doha Open", date(2025, 1, 10))


def test_all_dim_players_org_codes_map_to_continent() -> None:
    assert set(IOC_TO_CONTINENT) == set(IOC_TO_ISO2)
    assert continent_for_code("CHN") == "Asia"
    assert continent_for_code("GER") == "Europe"
    assert continent_for_code("REF") == "Neutral / Other"
    assert continent_for_code(None) == "Unknown"

    players_path = default_output_dir() / "dim_players.parquet"
    players = pl.read_parquet(players_path)
    orgs = {
        c.strip().upper()
        for c in players["organization_code"].drop_nulls().to_list()
        if c and c.strip()
    }
    valid_continents = {"Africa", "Americas", "Asia", "Europe", "Oceania"}
    for org in orgs:
        continent = continent_for_code(org)
        if org in NEUTRAL_ORG_CODES:
            assert continent == "Neutral / Other"
        else:
            assert continent in valid_continents, (
                f"Unmapped org {org!r} -> {continent!r}"
            )

    lb = build_glicko_leaderboard(_sample_ratings(), _sample_players(), gender="women")
    assert lb["continent"].to_list() == ["Asia", "Asia"]

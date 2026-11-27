"""Unit and UI tests for Asian vs European table-type Glicko-2 comparison."""

from __future__ import annotations

from datetime import date
from typing import Literal

import polars as pl
import pytest
from app.lib.table_types import (
    assign_origin_group,
    build_origin_avg_elo_chart,
    build_origin_edge_chart,
    build_origin_meta_analysis,
    build_table_type_comparison,
    build_table_type_event_options,
    student_t_two_tailed_p_value,
    summarize_brand_groups,
    welch_t_test,
)
from streamlit.testing.v1 import AppTest

from tt_stats.analytics.export_app_data import default_output_dir
from tt_stats.core.config import project_root


def _sample_players() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "player_sk": [1, 2, 3, 4],
            "player_name": [
                "Alice CHEN",
                "Cara SATO",
                "Dana KIM",
                "Bob MULLER",
            ],
            "organization_code": ["CHN", "JPN", "KOR", "GER"],
            "country_code": ["CHN", "JPN", "KOR", "GER"],
            "organization_name": ["China", "Japan", "Korea", "Germany"],
            "country_name": ["China", "Japan", "Korea", "Germany"],
            "style": ["Attacker", "Defender", "Attacker", "Attacker"],
            "grip": ["Shakehand", "Shakehand", "Penhold", "Shakehand"],
            "hand": ["Left", "Right", "Right", "Right"],
            "age": [24, 22, 26, 28],
            "head_shot": [None, None, None, None],
            "gender": ["F", "F", "F", "M"],
        }
    )


def _match_row(
    *,
    match_sk: int,
    event_sk: int,
    table_group: str,
    gender_category: str,
    rating_date: date,
    p1_sk: int,
    p2_sk: int,
    p1_post: float,
    p2_post: float,
    p1_score: float,
) -> dict[str, object]:
    return {
        "match_sk": match_sk,
        "event_sk": event_sk,
        "table_group": table_group,
        "gender_category": gender_category,
        "rating_date": rating_date,
        "p1_player_sk": p1_sk,
        "p2_player_sk": p2_sk,
        "p1_pre_rating": 1500.0,
        "p1_pre_rd": 200.0,
        "p1_pre_vol": 0.06,
        "p1_post_rating": p1_post,
        "p1_post_rd": 150.0,
        "p1_post_vol": 0.06,
        "p2_pre_rating": 1500.0,
        "p2_pre_rd": 200.0,
        "p2_pre_vol": 0.06,
        "p2_post_rating": p2_post,
        "p2_post_rd": 150.0,
        "p2_post_vol": 0.06,
        "p1_expected_win_prob": 0.5,
        "p1_actual_score": p1_score,
    }


def _sample_table_ratings() -> pl.DataFrame:
    rows = [
        # Asian women: Alice (1) plays 2 (2W, 1700.25), Cara (2) plays 2 (0W, 1380.0),
        # Dana (3) plays 2 (1W/1L, 1520.0)
        _match_row(
            match_sk=1,
            event_sk=10,
            table_group="Asian",
            gender_category="women",
            rating_date=date(2025, 1, 10),
            p1_sk=1,
            p2_sk=2,
            p1_post=1620.0,
            p2_post=1400.0,
            p1_score=1.0,
        ),
        _match_row(
            match_sk=2,
            event_sk=10,
            table_group="Asian",
            gender_category="women",
            rating_date=date(2025, 1, 11),
            p1_sk=1,
            p2_sk=3,
            p1_post=1700.25,
            p2_post=1480.0,
            p1_score=1.0,
        ),
        _match_row(
            match_sk=3,
            event_sk=11,
            table_group="Asian",
            gender_category="women",
            rating_date=date(2025, 3, 5),
            p1_sk=3,
            p2_sk=2,
            p1_post=1520.0,
            p2_post=1380.0,
            p1_score=1.0,
        ),
        # European women:
        # Before June: Alice (1) vs Cara (2) -> Alice 1580.0 (1W), Cara 1420.0 (0W)
        # In June: Cara beats Alice -> Cara ends at 1610.0 and Alice at 1590.10,
        # giving Alice +110.15 -> +110.2 Elo diff and Cara -230.0 Elo diff.
        _match_row(
            match_sk=4,
            event_sk=12,
            table_group="European",
            gender_category="women",
            rating_date=date(2025, 4, 1),
            p1_sk=1,
            p2_sk=2,
            p1_post=1580.0,
            p2_post=1420.0,
            p1_score=1.0,
        ),
        _match_row(
            match_sk=5,
            event_sk=13,
            table_group="European",
            gender_category="women",
            rating_date=date(2025, 6, 15),
            p1_sk=2,
            p2_sk=1,
            p1_post=1610.0,
            p2_post=1590.1,
            p1_score=1.0,
        ),
        # Dana (3) only has 1 European match -> excluded at min_matches_per_group=2
        _match_row(
            match_sk=6,
            event_sk=13,
            table_group="European",
            gender_category="women",
            rating_date=date(2025, 6, 16),
            p1_sk=3,
            p2_sk=4,
            p1_post=1510.0,
            p2_post=1490.0,
            p1_score=1.0,
        ),
    ]
    return pl.DataFrame(rows)


def test_build_table_type_comparison_diffs_sorting_and_min_matches() -> None:
    players = _sample_players()
    ratings = _sample_table_ratings()

    # With min_matches_per_group=2, Dana (2 Asian, 1 European) is excluded.
    df = build_table_type_comparison(
        ratings,
        players,
        gender="women",
        min_matches_per_group=2,
    )
    assert df.height == 2
    assert df["rank"].to_list() == [1, 2]
    # Cara: |1380.0 - 1610.0| = 230.0; Alice: |1700.25 - 1590.1| = 110.2
    assert df["player_sk"].to_list() == [2, 1]

    cara = df.row(0, named=True)
    assert cara["player_name"] == "Cara SATO"
    assert cara["continent"] == "Asia"
    assert cara["stronger_on"] == "European"
    assert cara["rating_diff"] == pytest.approx(-230.0)
    assert cara["abs_rating_diff"] == pytest.approx(230.0)
    assert cara["asian_win_pct"] == pytest.approx(0.0)
    assert cara["european_win_pct"] == pytest.approx(50.0)
    assert cara["win_pct_diff"] == pytest.approx(-50.0)
    assert cara["total_matches"] == 4
    assert cara["asian_matches"] == 2
    assert cara["european_matches"] == 2

    alice = df.row(1, named=True)
    assert alice["continent"] == "Asia"
    assert alice["stronger_on"] == "Asian"
    assert alice["rating_diff"] == pytest.approx(110.2)
    assert alice["asian_win_pct"] == pytest.approx(100.0)
    assert alice["european_win_pct"] == pytest.approx(50.0)
    assert alice["win_pct_diff"] == pytest.approx(50.0)
    assert alice["asian_wins"] == 2
    assert alice["asian_losses"] == 0
    assert alice["european_wins"] == 1
    assert alice["european_losses"] == 1

    # Lowering min_matches_per_group=1 brings Dana in (diff = 1520 - 1510 = +10.0).
    df_min1 = build_table_type_comparison(
        ratings,
        players,
        gender="women",
        min_matches_per_group=1,
    )
    assert df_min1["player_sk"].to_list() == [2, 1, 3]


def test_build_table_type_comparison_as_of_date_and_gender() -> None:
    players = _sample_players()
    ratings = _sample_table_ratings()

    # As of May 1 excludes June European matches -> only 1 European match each.
    df_may = build_table_type_comparison(
        ratings,
        players,
        gender="women",
        as_of_date=date(2025, 5, 1),
        min_matches_per_group=1,
    )
    assert df_may["player_sk"].to_list() == [1, 2]
    alice_may = df_may.filter(pl.col("player_sk") == 1).row(0, named=True)
    assert alice_may["european_rating"] == pytest.approx(1580.0)
    assert alice_may["european_matches"] == 1
    assert alice_may["rating_diff"] == pytest.approx(120.2)

    # No men's matches exist in sample -> empty with exact schema.
    df_men = build_table_type_comparison(
        ratings,
        players,
        gender="men",
        min_matches_per_group=1,
    )
    assert df_men.is_empty()
    assert "rating_diff" in df_men.columns
    assert "asian_rating" in df_men.columns


def test_build_table_type_comparison_empty_handling() -> None:
    empty_ratings = _sample_table_ratings().head(0)
    df = build_table_type_comparison(
        empty_ratings,
        _sample_players(),
        gender="women",
    )
    assert df.is_empty()
    assert df.columns[:5] == [
        "rank",
        "player_sk",
        "flag_url",
        "player_name",
        "country_name",
    ]


def test_build_table_type_event_options_and_brand_summary() -> None:
    ratings = _sample_table_ratings()
    events = pl.DataFrame(
        {
            "event_sk": [10, 11, 12, 13],
            "event_name": [
                "Doha Open",
                "Singapore Smash",
                "Frankfurt Champions",
                "Ljubljana Star",
            ],
        }
    )
    options = build_table_type_event_options(ratings, events, gender="women")
    assert options[0] == ("Latest (16/06/25 — All matches)", None)
    assert options[1] == ("16/06/25 · Ljubljana Star", date(2025, 6, 16))
    assert options[-1] == ("11/01/25 · Doha Open", date(2025, 1, 11))

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
    grouped = summarize_brand_groups(brands)
    assert grouped == {
        "Asian": ["Butterfly", "DHS"],
        "European": ["Tibhar"],
        "Other": ["Xiom"],
    }
    assert summarize_brand_groups(brands.head(0)) == {
        "Asian": [],
        "European": [],
        "Other": [],
    }


def test_table_types_smoke_on_real_parquet() -> None:
    data_dir = default_output_dir()
    table_ratings = pl.read_parquet(
        data_dir / "fct_table_type_glicko_match_ratings.parquet"
    )
    table_brands = pl.read_parquet(data_dir / "dim_table_brands.parquet")
    players = pl.read_parquet(data_dir / "dim_players.parquet")

    grouped = summarize_brand_groups(table_brands)
    assert set(grouped.keys()) == {"Asian", "European", "Other"}
    assert len(grouped["Asian"]) > 0
    assert len(grouped["European"]) > 0

    genders: tuple[Literal["men", "women"], ...] = ("men", "women")
    for gender in genders:
        comp = build_table_type_comparison(
            table_ratings,
            players,
            gender=gender,
            min_matches_per_group=5,
        )
        assert comp.height > 10
        assert comp["rank"].to_list()[:3] == [1, 2, 3]
        assert comp["abs_rating_diff"].is_sorted(descending=True)
        assert (comp["asian_matches"] >= 5).all()
        assert (comp["european_matches"] >= 5).all()


def test_table_types_page_apptest() -> None:
    page_path = project_root() / "app" / "pages" / "7_Table_Types.py"
    at = AppTest.from_file(page_path).run(timeout=30)
    assert not at.exception
    assert len(at.dataframe) == 2

    default_cols = list(at.dataframe[0].value.columns)
    assert "stronger_on" in default_cols
    assert "rating_diff" in default_cols
    assert "win_pct_diff" in default_cols
    assert "total_matches" in default_cols
    assert "asian_rating" not in default_cols
    assert "european_rating" not in default_cols

    meta_cols = list(at.dataframe[1].value.columns)
    assert "origin_group" in meta_cols
    assert "significance_label" in meta_cols
    assert len(at.dataframe[1].value) == 3

    at.multiselect(key="table_types_continent_men").set_value(["Europe"]).run(
        timeout=30
    )
    assert not at.exception
    assert len(at.dataframe) == 2
    assert len(at.dataframe[0].value) > 0

    at.toggle(key="table_types_show_full_details").set_value(True).run(timeout=30)
    assert not at.exception
    detail_cols = list(at.dataframe[0].value.columns)
    for col in ("asian_rating", "european_rating", "asian_win_pct", "european_win_pct"):
        assert col in detail_cols

    at.segmented_control(key="table_types_pool_choice").set_value(
        "Women's Singles"
    ).run(timeout=30)
    assert not at.exception
    assert len(at.dataframe) == 2
    assert len(at.dataframe[0].value) > 0


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


def test_origin_meta_analysis_and_charts() -> None:
    sample = pl.DataFrame(
        {
            "org": ["CHN", "CHN", "CHN", "JPN", "KOR", "GER", "SWE", "BRA"],
            "continent": [
                "Asia",
                "Asia",
                "Asia",
                "Asia",
                "Asia",
                "Europe",
                "Europe",
                "Americas",
            ],
            "asian_rating": [
                2100.0,
                2050.0,
                2000.0,
                1850.0,
                1820.0,
                1650.0,
                1640.0,
                1660.0,
            ],
            "european_rating": [
                1950.0,
                1920.0,
                1890.0,
                1840.0,
                1810.0,
                1730.0,
                1720.0,
                1740.0,
            ],
            "rating_diff": [150.0, 130.0, 110.0, 10.0, 10.0, -80.0, -80.0, -80.0],
            "win_pct_diff": [12.0, 10.0, 8.0, 1.0, 0.0, -6.0, -5.0, -7.0],
        }
    )
    grouped = assign_origin_group(sample)
    assert grouped["origin_group"].to_list() == [
        "Chinese",
        "Chinese",
        "Chinese",
        "Non-Chinese Asian",
        "Non-Chinese Asian",
        "Rest of World",
        "Rest of World",
        "Rest of World",
    ]

    meta = build_origin_meta_analysis(sample)
    assert meta["origin_group"].to_list() == [
        "Chinese",
        "Non-Chinese Asian",
        "Rest of World",
    ]
    assert meta["players"].to_list() == [3, 2, 3]
    chn = meta.filter(pl.col("origin_group") == "Chinese").row(0, named=True)
    assert chn["mean_rating_diff"] == pytest.approx(130.0)
    assert chn["rel_elo_vs_row"] == pytest.approx(210.0)
    assert chn["welch_p_vs_row"] is not None
    assert chn["welch_p_vs_row"] < 0.05

    row_entry = meta.filter(pl.col("origin_group") == "Rest of World").row(
        0, named=True
    )
    assert row_entry["significance_label"] == "Baseline"
    assert row_entry["welch_p_vs_row"] is None

    avg_fig = build_origin_avg_elo_chart(meta)
    edge_fig = build_origin_edge_chart(meta)
    assert len(avg_fig.data) == 2
    assert len(edge_fig.data) == 1

    empty_meta = build_origin_meta_analysis(sample.head(0))
    assert empty_meta.is_empty()
    assert len(build_origin_avg_elo_chart(empty_meta).data) == 0
    assert len(build_origin_edge_chart(empty_meta).data) == 0

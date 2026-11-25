"""Unit tests for Service Win Rate aggregations, filters, charts, and UI."""

from __future__ import annotations

import polars as pl
import pytest
from app.lib.serve_receive import (
    build_player_table_view,
    build_serve_receive_scatter,
    compute_global_kpis,
    enrich_serve_receive_stats,
    filter_serve_receive_rows,
)
from streamlit.testing.v1 import AppTest

from tt_stats.core.config import project_root


def _sample_stats_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "player_sk": [1, 2, 3],
            "Player": ["Alice CHEN", "Bob MULLER", "Cara TAN"],
            "Organization": ["CHN", "GER", "SGP"],
            "Gender": ["F", "M", "Women"],
            "Style": ["Defender", None, "Attacker"],
            "Grip": ["Shakehand", "Penhold", ""],
            "Hand": ["Right", None, "Left"],
            "Matches": [20, 12, 4],
            "Points Played": [200, 100, 50],
            "Points Won": [110, 48, 25],
            "Points Lost": [90, 52, 25],
            "Serve Points": [100, 50, 25],
            "Serve Won": [60, 28, 15],
            "Serve Lost": [40, 22, 10],
            "Receive Points": [100, 50, 25],
            "Receive Won": [50, 20, 10],
            "Receive Lost": [50, 30, 15],
        }
    )


def test_enrich_and_global_kpis() -> None:
    enriched = enrich_serve_receive_stats(_sample_stats_df())

    alice = enriched.filter(pl.col("player_sk") == 1).row(0, named=True)
    assert alice["Gender"] == "Women"
    assert alice["Point %"] == 55.0
    assert alice["Serve Win %"] == 60.0
    assert alice["Receive Win %"] == 50.0
    assert alice["Serve - Total (pp)"] == 5.0
    assert alice["Receive - Total (pp)"] == -5.0
    assert alice["Serve - Receive (pp)"] == 10.0

    bob = enriched.filter(pl.col("player_sk") == 2).row(0, named=True)
    assert bob["Gender"] == "Men"
    assert bob["Style"] == "Unknown"
    assert bob["Hand"] == "Unknown"

    cara = enriched.filter(pl.col("player_sk") == 3).row(0, named=True)
    assert cara["Grip"] == "Unknown"

    kpis = compute_global_kpis(enriched)
    assert kpis.total_matches == (20 + 12 + 4) // 2
    assert kpis.total_points == 175
    assert kpis.global_serve_win_pct == pytest.approx(100.0 * 103 / 175, abs=0.01)
    assert kpis.global_receive_win_pct == pytest.approx(100.0 * 80 / 175, abs=0.01)
    assert kpis.mens_serve_win_pct == pytest.approx(56.0, abs=0.01)
    assert kpis.womens_serve_win_pct == pytest.approx(60.0, abs=0.01)
    assert kpis.n_players == 3


def test_filters_and_metric_view_table_columns() -> None:
    enriched = enrich_serve_receive_stats(_sample_stats_df())

    filtered = filter_serve_receive_rows(
        enriched,
        gender="All",
        min_matches=10,
        search_query="alice",
    )
    assert filtered.height == 1
    assert filtered["Player"][0] == "Alice CHEN"

    all_view = build_player_table_view(enriched, metric_view="All")
    assert all_view.columns == [
        "Player",
        "Org",
        "Style",
        "Matches",
        "Total Won",
        "Total Lost",
        "Total Win %",
        "Serve Won",
        "Serve Lost",
        "Serve Win %",
        "Receive Won",
        "Receive Lost",
        "Receive Win %",
    ]
    assert all_view["Player"][0] in {"Alice CHEN", "Cara TAN"}

    serve_view = build_player_table_view(enriched, metric_view="Serve")
    assert serve_view.columns == [
        "Player",
        "Org",
        "Style",
        "Matches",
        "Serve Won",
        "Serve Lost",
        "Serve Win %",
    ]

    receive_view = build_player_table_view(enriched, metric_view="Receive")
    assert receive_view.columns == [
        "Player",
        "Org",
        "Style",
        "Matches",
        "Receive Won",
        "Receive Lost",
        "Receive Win %",
    ]

    total_view = build_player_table_view(enriched, metric_view="Total")
    assert total_view.columns == [
        "Player",
        "Org",
        "Style",
        "Matches",
        "Total Won",
        "Total Lost",
        "Total Win %",
    ]


def test_scatter_builder_handles_comparisons_and_empty() -> None:
    enriched = enrich_serve_receive_stats(_sample_stats_df())

    fig_srv, summary_srv = build_serve_receive_scatter(
        enriched,
        comparison="serve_vs_total",
        title="Serve vs Total",
        highlight_player="Alice CHEN",
    )
    assert summary_srv.n_players == 3
    assert summary_srv.n_defenders == 1
    assert summary_srv.n_attackers == 1
    assert summary_srv.mean_delta_pp is not None
    assert summary_srv.mean_delta_pp > 0
    assert len(fig_srv.data) >= 4

    fig_rcv, summary_rcv = build_serve_receive_scatter(
        enriched,
        comparison="receive_vs_total",
        title="Receive vs Total",
    )
    assert summary_rcv.mean_delta_pp is not None
    assert summary_rcv.mean_delta_pp < 0
    assert len(fig_rcv.data) >= 3

    _, empty_summary = build_serve_receive_scatter(
        enriched.head(0),
        comparison="serve_vs_total",
        title="Empty",
    )
    assert empty_summary.n_players == 0


def test_service_win_rate_page_renders_and_controls_work() -> None:
    app_dir = project_root() / "app"
    page = AppTest.from_file(app_dir / "pages" / "4_Service_Win_Rate.py").run(
        timeout=30
    )
    assert not page.exception

    subheaders = [s.value for s in page.subheader]
    assert subheaders[:2] == [
        "1. Player Service & Receive Leaderboard",
        "2. Serve & Receive vs Total Point Win %",
    ]

    assert len(page.slider) == 2
    for slider in page.slider:
        assert slider.value == 10

    assert len(page.dataframe) == 1

    # Switch Metric View to Serve, search for a player, and switch chart comparison
    page.segmented_control(key="sr_table_view").set_value("Serve")
    page.text_input(key="sr_table_search").set_value("HARIMOTO")
    page.segmented_control(key="sr_chart_comparison").set_value("Receive % vs Total %")
    page.slider(key="sr_chart_min_matches").set_value(5)
    page.run(timeout=30)
    assert not page.exception

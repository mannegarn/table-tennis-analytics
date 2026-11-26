"""Serve & Receive point-level calculations, table builders, and scatter plots.

This module is pure Python (Polars + NumPy + Plotly) with no Streamlit UI calls
so all calculations, filtering, and chart builders can be unit-tested directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
import plotly.graph_objects as go  # type: ignore[import-untyped]
import polars as pl
from numpy.typing import NDArray

from .charts import (
    BINOMIAL_CURVE_COLOR,
    HIGHLIGHT_MARKER_COLOR,
    STYLE_MARKERS,
    style_figure,
)

MetricView = Literal["All", "Total", "Serve", "Receive"]
ChartComparison = Literal["serve_vs_total", "receive_vs_total"]

COMPARISON_LABELS: dict[str, ChartComparison] = {
    "Serve % vs Total %": "serve_vs_total",
    "Receive % vs Total %": "receive_vs_total",
}

COMPARISON_METADATA: dict[ChartComparison, tuple[str, str, str, str]] = {
    "serve_vs_total": (
        "Point %",
        "Serve Win %",
        "Total Point Win %",
        "Serve Point Win %",
    ),
    "receive_vs_total": (
        "Point %",
        "Receive Win %",
        "Total Point Win %",
        "Receive Point Win %",
    ),
}


@dataclass(frozen=True)
class GlobalServeReceiveKPIs:
    """Tour-wide serve/receive averages across all commentary-covered matches."""

    total_matches: int
    total_points: int
    global_serve_win_pct: float | None
    global_receive_win_pct: float | None
    mens_serve_win_pct: float | None
    womens_serve_win_pct: float | None
    mens_matches: int
    womens_matches: int
    mens_points: int
    womens_points: int
    n_players: int


@dataclass(frozen=True)
class ServeReceiveChartSummary:
    """Compact summary statistics displayed beneath the scatter plot."""

    n_players: int
    n_attackers: int
    n_defenders: int
    mean_delta_pp: float | None
    attacker_mean_delta_pp: float | None
    defender_mean_delta_pp: float | None
    mean_serve_minus_receive_pp: float | None


def _clean_text_attr(col_name: str, alias: str) -> pl.Expr:
    cleaned = pl.col(col_name).cast(pl.String).str.strip_chars()
    return (
        pl.when(cleaned.is_null() | (cleaned == ""))
        .then(pl.lit("Unknown"))
        .otherwise(cleaned)
        .alias(alias)
    )


def enrich_serve_receive_stats(df: pl.DataFrame) -> pl.DataFrame:
    """Normalize profile columns and compute point, serve, and receive win rates."""
    if df.is_empty():
        return df

    normalized = df.with_columns(
        pl.col("Player").fill_null("Unknown Player"),
        pl.col("Organization").fill_null(""),
        pl.when(pl.col("Gender") == "M")
        .then(pl.lit("Men"))
        .when(pl.col("Gender") == "F")
        .then(pl.lit("Women"))
        .when(pl.col("Gender").is_in(["Men", "Women"]))
        .then(pl.col("Gender"))
        .otherwise(pl.lit("Unknown"))
        .alias("Gender"),
        _clean_text_attr("Style", "Style"),
        _clean_text_attr("Grip", "Grip"),
        _clean_text_attr("Hand", "Hand"),
    ).filter(
        (pl.col("Points Played") > 0)
        & (pl.col("Serve Points") > 0)
        & (pl.col("Receive Points") > 0)
    )

    return normalized.with_columns(
        (100.0 * pl.col("Points Won") / pl.col("Points Played"))
        .round(2)
        .alias("Point %"),
        (100.0 * pl.col("Serve Won") / pl.col("Serve Points"))
        .round(2)
        .alias("Serve Win %"),
        (100.0 * pl.col("Receive Won") / pl.col("Receive Points"))
        .round(2)
        .alias("Receive Win %"),
    ).with_columns(
        (pl.col("Serve Win %") - pl.col("Point %"))
        .round(2)
        .alias("Serve - Total (pp)"),
        (pl.col("Receive Win %") - pl.col("Point %"))
        .round(2)
        .alias("Receive - Total (pp)"),
        (pl.col("Serve Win %") - pl.col("Receive Win %"))
        .round(2)
        .alias("Serve - Receive (pp)"),
    )


def _weighted_pct(df: pl.DataFrame, won_col: str, total_col: str) -> float | None:
    if df.is_empty():
        return None
    total = int(df[total_col].sum())
    if total <= 0:
        return None
    won = int(df[won_col].sum())
    return round(100.0 * won / total, 2)


def compute_global_kpis(df: pl.DataFrame) -> GlobalServeReceiveKPIs:
    """Compute exact global and gender-split serve/receive KPIs from player totals.

    Each commentary-covered match has two player entries (server and receiver),
    so summing ``Serve Points`` across all players gives the exact total number
    of commentary points, and summing ``Matches`` divided by 2 gives the exact
    match count.
    """
    if df.is_empty():
        return GlobalServeReceiveKPIs(
            total_matches=0,
            total_points=0,
            global_serve_win_pct=None,
            global_receive_win_pct=None,
            mens_serve_win_pct=None,
            womens_serve_win_pct=None,
            mens_matches=0,
            womens_matches=0,
            mens_points=0,
            womens_points=0,
            n_players=0,
        )

    men = df.filter(pl.col("Gender") == "Men")
    women = df.filter(pl.col("Gender") == "Women")

    return GlobalServeReceiveKPIs(
        total_matches=int(df["Matches"].sum()) // 2,
        total_points=int(df["Serve Points"].sum()),
        global_serve_win_pct=_weighted_pct(df, "Serve Won", "Serve Points"),
        global_receive_win_pct=_weighted_pct(df, "Receive Won", "Receive Points"),
        mens_serve_win_pct=_weighted_pct(men, "Serve Won", "Serve Points"),
        womens_serve_win_pct=_weighted_pct(women, "Serve Won", "Serve Points"),
        mens_matches=int(men["Matches"].sum()) // 2 if not men.is_empty() else 0,
        womens_matches=int(women["Matches"].sum()) // 2 if not women.is_empty() else 0,
        mens_points=int(men["Serve Points"].sum()) if not men.is_empty() else 0,
        womens_points=int(women["Serve Points"].sum()) if not women.is_empty() else 0,
        n_players=df.height,
    )


def filter_serve_receive_rows(
    df: pl.DataFrame,
    *,
    gender: str = "All",
    min_matches: int = 10,
    search_query: str = "",
    styles: Sequence[str] | None = None,
    grips: Sequence[str] | None = None,
    hands: Sequence[str] | None = None,
) -> pl.DataFrame:
    """Filter player serve/receive rows by gender, min matches, search, and profile."""
    filtered = df.filter(pl.col("Matches") >= min_matches)
    if gender != "All":
        filtered = filtered.filter(pl.col("Gender") == gender)
    if styles is not None:
        filtered = filtered.filter(pl.col("Style").is_in(list(styles)))
    if grips is not None:
        filtered = filtered.filter(pl.col("Grip").is_in(list(grips)))
    if hands is not None:
        filtered = filtered.filter(pl.col("Hand").is_in(list(hands)))

    query = search_query.strip().lower()
    if query:
        filtered = filtered.filter(
            pl.col("Player").str.to_lowercase().str.contains(query, literal=True)
        )
    return filtered


def build_player_table_view(
    df: pl.DataFrame,
    *,
    metric_view: MetricView = "All",
) -> pl.DataFrame:
    """Select and sort table columns according to the active Metric View."""
    total_cols = [
        pl.col("Points Won").alias("Total Won"),
        pl.col("Points Lost").alias("Total Lost"),
        pl.col("Point %").alias("Total Win %"),
    ]
    serve_cols = [
        pl.col("Serve Won"),
        pl.col("Serve Lost"),
        pl.col("Serve Win %"),
        pl.col("Serve - Total (pp)").alias("Serve Delta"),
    ]
    receive_cols = [
        pl.col("Receive Won"),
        pl.col("Receive Lost"),
        pl.col("Receive Win %"),
        pl.col("Receive - Total (pp)").alias("Receive Delta"),
    ]

    # For single-metric views, place Win % and Delta directly beside Player
    # so key stats stay immediately visible on narrow mobile screens.
    if metric_view == "Total":
        selected = [
            pl.col("Player"),
            pl.col("Point %").alias("Total Win %"),
            pl.col("Points Won").alias("Total Won"),
            pl.col("Points Lost").alias("Total Lost"),
            pl.col("Matches"),
            pl.col("Organization").alias("Org"),
            pl.col("Style"),
        ]
        sort_cols = ["Total Win %", "Matches"]
    elif metric_view == "Serve":
        selected = [
            pl.col("Player"),
            pl.col("Serve Win %"),
            pl.col("Serve - Total (pp)").alias("Serve Delta"),
            pl.col("Serve Won"),
            pl.col("Serve Lost"),
            pl.col("Matches"),
            pl.col("Organization").alias("Org"),
            pl.col("Style"),
        ]
        sort_cols = ["Serve Win %", "Matches"]
    elif metric_view == "Receive":
        selected = [
            pl.col("Player"),
            pl.col("Receive Win %"),
            pl.col("Receive - Total (pp)").alias("Receive Delta"),
            pl.col("Receive Won"),
            pl.col("Receive Lost"),
            pl.col("Matches"),
            pl.col("Organization").alias("Org"),
            pl.col("Style"),
        ]
        sort_cols = ["Receive Win %", "Matches"]
    else:
        selected = [
            pl.col("Player"),
            pl.col("Organization").alias("Org"),
            pl.col("Style"),
            pl.col("Matches"),
            *total_cols,
            *serve_cols,
            *receive_cols,
        ]
        sort_cols = ["Serve Win %", "Matches"]

    return df.select(selected).sort(sort_cols, descending=[True, True])


def _axis_range(values: NDArray[np.float64], pad: float = 2.5) -> list[float]:
    """Compute a padded [min, max] percentage axis range clipped to [0, 100]."""
    if values.size == 0:
        return [0.0, 100.0]
    lo = max(0.0, float(np.floor((values.min() - pad) / 5.0) * 5.0))
    hi = min(100.0, float(np.ceil((values.max() + pad) / 5.0) * 5.0))
    if hi - lo < 10.0:
        mid = 0.5 * (lo + hi)
        lo = max(0.0, mid - 5.0)
        hi = min(100.0, mid + 5.0)
    return [lo, hi]


def build_serve_receive_scatter(
    df: pl.DataFrame,
    *,
    comparison: ChartComparison,
    title: str,
    gender_label: str = "All",
    highlight_player: str | None = None,
    height: int = 480,
) -> tuple[go.Figure, ServeReceiveChartSummary]:
    """Build a theme-aware Serve/Receive vs Total Point % scatter plot."""
    x_col, y_col, x_label, y_label = COMPARISON_METADATA[comparison]

    clean_df = df.filter(
        pl.col(x_col).is_not_null()
        & pl.col(y_col).is_not_null()
        & pl.col(x_col).is_finite()
        & pl.col(y_col).is_finite()
    )

    if clean_df.is_empty():
        empty_fig = go.Figure()
        empty_fig.add_annotation(
            text="No players match the current filter selection.",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font={"size": 14},
        )
        empty_fig = style_figure(empty_fig, height=320)
        return empty_fig, ServeReceiveChartSummary(
            n_players=0,
            n_attackers=0,
            n_defenders=0,
            mean_delta_pp=None,
            attacker_mean_delta_pp=None,
            defender_mean_delta_pp=None,
            mean_serve_minus_receive_pp=None,
        )

    xs = clean_df[x_col].to_numpy().astype(np.float64)
    ys = clean_df[y_col].to_numpy().astype(np.float64)
    deltas = ys - xs

    enriched = clean_df.with_columns(
        pl.Series("_delta_pp", np.round(deltas, 2)),
        (
            pl.col("Style")
            + pl.lit(" · ")
            + pl.col("Grip")
            + pl.lit(" · ")
            + pl.col("Hand")
        ).alias("_profile"),
    )

    fig = go.Figure()

    # 1. Player scatter traces (ordered back-to-front so Defenders pop on top)
    for spec in STYLE_MARKERS:
        subset = enriched.filter(pl.col("Style") == spec.label)
        if subset.is_empty():
            continue

        customdata = np.column_stack(
            [
                subset["Player"].to_list(),
                subset["Organization"].to_list(),
                subset["_profile"].to_list(),
                subset["Matches"].to_list(),
                subset["Points Played"].to_list(),
                subset["_delta_pp"].to_list(),
            ]
        )

        fig.add_trace(
            go.Scatter(
                x=subset[x_col].to_list(),
                y=subset[y_col].to_list(),
                mode="markers",
                name=f"{spec.label} ({subset.height:,})",
                customdata=customdata,
                marker={
                    "color": spec.color,
                    "symbol": spec.symbol,
                    "size": spec.size,
                    "opacity": spec.opacity,
                    "line": {"width": spec.line_width, "color": spec.line_color},
                },
                hovertemplate=(
                    "<b>%{customdata[0]}</b> (%{customdata[1]})<br>"
                    "%{customdata[2]}<br>"
                    "Commentary Matches: %{customdata[3]:,} · "
                    "Points: %{customdata[4]:,}<br>"
                    f"{x_label}: %{{x:.1f}}%<br>"
                    f"{y_label}: %{{y:.1f}}%<br>"
                    "Delta vs Total: %{customdata[5]:+.1f} pp<extra></extra>"
                ),
            )
        )

    # 2. y = x parity diagonal line (highlights server advantage above/below parity)
    x_range = _axis_range(xs, pad=2.5)
    y_range = _axis_range(np.concatenate([ys, xs]), pad=3.0)
    diag_lo = max(x_range[0], y_range[0])
    diag_hi = min(x_range[1], y_range[1])
    if diag_hi > diag_lo:
        fig.add_trace(
            go.Scatter(
                x=[diag_lo, diag_hi],
                y=[diag_lo, diag_hi],
                mode="lines",
                name="y = x (parity)",
                line={"color": BINOMIAL_CURVE_COLOR, "width": 2.2, "dash": "dash"},
                hovertemplate=(
                    "<b>y = x (parity)</b><br>"
                    f"{x_label} = {y_label}: %{{x:.1f}}%<extra></extra>"
                ),
            )
        )

    # 3. Optional highlighted player star marker
    if highlight_player:
        hl = enriched.filter(pl.col("Player") == highlight_player)
        if not hl.is_empty():
            row = hl.row(0, named=True)
            fig.add_trace(
                go.Scatter(
                    x=[row[x_col]],
                    y=[row[y_col]],
                    mode="markers+text",
                    name=f"Selected: {row['Player']}",
                    text=[row["Player"]],
                    textposition="top center",
                    marker={
                        "color": HIGHLIGHT_MARKER_COLOR,
                        "symbol": "star",
                        "size": 15,
                        "line": {"width": 1.5, "color": "#FFFFFF"},
                    },
                    hovertemplate=(
                        f"<b>{row['Player']}</b> ({row['Organization']})<br>"
                        f"{row['_profile']}<br>"
                        f"Commentary Matches: {row['Matches']:,} · "
                        f"Points: {row['Points Played']:,}<br>"
                        f"{x_label}: %{{x:.1f}}%<br>"
                        f"{y_label}: %{{y:.1f}}%<br>"
                        f"Delta vs Total: {row['_delta_pp']:+.1f} pp<extra></extra>"
                    ),
                )
            )

    n_attackers = enriched.filter(pl.col("Style") == "Attacker").height
    n_defenders = enriched.filter(pl.col("Style") == "Defender").height

    scope_parts = [f"n = {enriched.height:,} players", "Bo5 + Bo7 combined"]
    if gender_label != "All":
        scope_parts.append(gender_label)
    subtitle = " · ".join(scope_parts)

    fig = style_figure(fig, height=height, legend_bottom=True)
    fig.update_layout(
        title={
            "text": f"<b>{title}</b><br><sup>{subtitle}</sup>",
            "x": 0.01,
            "xanchor": "left",
            "font": {"size": 16},
        },
        margin={"l": 52, "r": 18, "t": 68, "b": 122},
    )

    if x_range[0] < 50.0 < x_range[1]:
        fig.add_vline(
            x=50.0,
            line_width=1,
            line_dash="dot",
            line_color="rgba(128, 128, 128, 0.35)",
        )
    if y_range[0] < 50.0 < y_range[1]:
        fig.add_hline(
            y=50.0,
            line_width=1,
            line_dash="dot",
            line_color="rgba(128, 128, 128, 0.35)",
        )

    fig.update_xaxes(title_text=x_label, range=x_range, ticksuffix="%")
    fig.update_yaxes(title_text=y_label, range=y_range, ticksuffix="%")

    att_deltas = enriched.filter(pl.col("Style") == "Attacker")["_delta_pp"]
    def_deltas = enriched.filter(pl.col("Style") == "Defender")["_delta_pp"]
    srv_rcv_gap = enriched["Serve - Receive (pp)"]

    summary = ServeReceiveChartSummary(
        n_players=enriched.height,
        n_attackers=n_attackers,
        n_defenders=n_defenders,
        mean_delta_pp=float(np.mean(deltas)),
        attacker_mean_delta_pp=(
            float(att_deltas.mean()) if not att_deltas.is_empty() else None  # type: ignore[arg-type]
        ),
        defender_mean_delta_pp=(
            float(def_deltas.mean()) if not def_deltas.is_empty() else None  # type: ignore[arg-type]
        ),
        mean_serve_minus_receive_pp=(
            float(srv_rcv_gap.mean()) if not srv_rcv_gap.is_empty() else None  # type: ignore[arg-type]
        ),
    )
    return fig, summary

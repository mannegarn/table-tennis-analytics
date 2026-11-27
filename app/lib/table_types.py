"""Asian vs European table-type Glicko-2 comparison helpers.

This module is pure Python + Polars with no Streamlit UI calls so table-type
aggregations, time-travel filtering, and brand-group summaries can be
unit-tested directly.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date
from typing import Literal

import plotly.graph_objects as go  # type: ignore[import-untyped]
import polars as pl

from .charts import style_figure
from .glicko import build_as_of_event_options, build_glicko_leaderboard

ORIGIN_GROUPS: tuple[str, ...] = ("Chinese", "Non-Chinese Asian", "Rest of World")

ORIGIN_GROUP_COLORS: dict[str, str] = {
    "Chinese": "#F59E0B",
    "Non-Chinese Asian": "#34D399",
    "Rest of World": "#38BDF8",
}


def _empty_table_type_comparison() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "rank": pl.Int64,
            "player_sk": pl.Int64,
            "flag_url": pl.String,
            "player_name": pl.String,
            "country_name": pl.String,
            "org": pl.String,
            "continent": pl.String,
            "age": pl.Int64,
            "hand": pl.String,
            "grip": pl.String,
            "style": pl.String,
            "stronger_on": pl.String,
            "rating_diff": pl.Float64,
            "win_pct_diff": pl.Float64,
            "abs_rating_diff": pl.Float64,
            "abs_win_pct_diff": pl.Float64,
            "total_matches": pl.Int64,
            "asian_rating": pl.Float64,
            "european_rating": pl.Float64,
            "asian_win_pct": pl.Float64,
            "european_win_pct": pl.Float64,
            "asian_matches": pl.Int64,
            "asian_wins": pl.Int64,
            "asian_losses": pl.Int64,
            "european_matches": pl.Int64,
            "european_wins": pl.Int64,
            "european_losses": pl.Int64,
        }
    )


def build_table_type_comparison(
    table_ratings: pl.DataFrame,
    players: pl.DataFrame,
    *,
    gender: Literal["men", "women"],
    as_of_date: date | None = None,
    min_matches_per_group: int = 5,
) -> pl.DataFrame:
    """Compare Glicko-2 ratings and win rates on Asian vs European tables."""
    if table_ratings.is_empty():
        return _empty_table_type_comparison()

    asian_lb = build_glicko_leaderboard(
        table_ratings.filter(pl.col("table_group") == "Asian"),
        players,
        gender=gender,
        as_of_date=as_of_date,
        max_rd=None,
        min_matches=min_matches_per_group,
    )
    euro_lb = build_glicko_leaderboard(
        table_ratings.filter(pl.col("table_group") == "European"),
        players,
        gender=gender,
        as_of_date=as_of_date,
        max_rd=None,
        min_matches=min_matches_per_group,
    )
    if asian_lb.is_empty() or euro_lb.is_empty():
        return _empty_table_type_comparison()

    asian_cols = asian_lb.select(
        "player_sk",
        "flag_url",
        "player_name",
        "country_name",
        "org",
        "continent",
        "age",
        "hand",
        "grip",
        "style",
        pl.col("rating").alias("asian_rating"),
        pl.col("win_pct").alias("asian_win_pct"),
        pl.col("matches").alias("asian_matches"),
        pl.col("wins").alias("asian_wins"),
        pl.col("losses").alias("asian_losses"),
    )
    euro_cols = euro_lb.select(
        "player_sk",
        pl.col("rating").alias("european_rating"),
        pl.col("win_pct").alias("european_win_pct"),
        pl.col("matches").alias("european_matches"),
        pl.col("wins").alias("european_wins"),
        pl.col("losses").alias("european_losses"),
    )

    joined = asian_cols.join(euro_cols, on="player_sk", how="inner")
    if joined.is_empty():
        return _empty_table_type_comparison()

    with_diffs = joined.with_columns(
        (pl.col("asian_rating") - pl.col("european_rating"))
        .round(1)
        .alias("rating_diff"),
        (pl.col("asian_win_pct") - pl.col("european_win_pct"))
        .round(1)
        .alias("win_pct_diff"),
        (pl.col("asian_matches") + pl.col("european_matches")).alias("total_matches"),
    ).with_columns(
        pl.col("rating_diff").abs().alias("abs_rating_diff"),
        pl.col("win_pct_diff").abs().alias("abs_win_pct_diff"),
        pl.when(pl.col("rating_diff") > 0)
        .then(pl.lit("Asian"))
        .when(pl.col("rating_diff") < 0)
        .then(pl.lit("European"))
        .otherwise(pl.lit("Even"))
        .alias("stronger_on"),
    )

    return (
        with_diffs.sort(
            ["abs_rating_diff", "abs_win_pct_diff", "total_matches", "player_sk"],
            descending=[True, True, True, False],
        )
        .with_columns(pl.int_range(1, pl.len() + 1, dtype=pl.Int64).alias("rank"))
        .select(
            "rank",
            "player_sk",
            "flag_url",
            "player_name",
            "country_name",
            "org",
            "continent",
            "age",
            "hand",
            "grip",
            "style",
            "stronger_on",
            "rating_diff",
            "win_pct_diff",
            "abs_rating_diff",
            "abs_win_pct_diff",
            "total_matches",
            "asian_rating",
            "european_rating",
            "asian_win_pct",
            "european_win_pct",
            "asian_matches",
            "asian_wins",
            "asian_losses",
            "european_matches",
            "european_wins",
            "european_losses",
        )
    )


def build_table_type_event_options(
    table_ratings: pl.DataFrame,
    events: pl.DataFrame,
    *,
    gender: Literal["men", "women"],
) -> list[tuple[str, date | None]]:
    """Return ``(label, as_of_date)`` dropdown options newest-first."""
    scoped = table_ratings.filter(pl.col("table_group").is_in(["Asian", "European"]))
    return build_as_of_event_options(scoped, events, gender=gender)


def summarize_brand_groups(table_brands: pl.DataFrame) -> dict[str, list[str]]:
    """Return sorted ``table_sponsor`` lists keyed by Asian, European, and Other."""
    groups: dict[str, set[str]] = {
        "Asian": set(),
        "European": set(),
        "Other": set(),
    }
    if table_brands.is_empty():
        return {key: [] for key in groups}

    for row in table_brands.select("table_sponsor", "table_group").to_dicts():
        sponsor_raw = row.get("table_sponsor")
        group_raw = row.get("table_group")
        if not isinstance(sponsor_raw, str) or not isinstance(group_raw, str):
            continue
        sponsor = sponsor_raw.strip()
        group = group_raw.strip()
        if sponsor and group in groups:
            groups[group].add(sponsor)

    return {key: sorted(values) for key, values in groups.items()}


def assign_origin_group(df: pl.DataFrame) -> pl.DataFrame:
    """Classify players into Chinese, Non-Chinese Asian, or Rest of World."""
    if df.is_empty():
        return df.with_columns(pl.lit(None, dtype=pl.String).alias("origin_group"))
    return df.with_columns(
        pl.when(pl.col("org") == "CHN")
        .then(pl.lit("Chinese"))
        .when(pl.col("continent") == "Asia")
        .then(pl.lit("Non-Chinese Asian"))
        .otherwise(pl.lit("Rest of World"))
        .alias("origin_group")
    )


def _betacf(a: float, b: float, x: float) -> float:
    """Evaluate continued fraction for incomplete beta via modified Lentz method."""
    max_iter = 200
    eps = 3.0e-12
    fpmin = 1.0e-30

    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d

    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c

        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """Return regularized incomplete beta function ``I_x(a, b)`` for ``0 <= x <= 1``."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log(1.0 - x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def student_t_two_tailed_p_value(t_stat: float, df: float) -> float | None:
    """Return exact two-tailed Student's t-distribution p-value."""
    if df <= 0.0 or not math.isfinite(t_stat) or not math.isfinite(df):
        return None
    if t_stat == 0.0:
        return 1.0
    x = df / (df + t_stat * t_stat)
    p = _regularized_incomplete_beta(df * 0.5, 0.5, x)
    return max(0.0, min(1.0, p))


def _t_critical_95(df: float) -> float:
    """Return two-tailed 95% critical t value for ``df >= 1``."""
    if df <= 0.0 or not math.isfinite(df):
        return 1.959964
    lo, hi = 1.95, 13.0
    for _ in range(44):
        mid = 0.5 * (lo + hi)
        p = student_t_two_tailed_p_value(mid, df)
        if p is not None and p > 0.05:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _mean_and_sample_var(values: Sequence[float]) -> tuple[float, float]:
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, var


def paired_t_test(
    values: Sequence[float],
) -> tuple[float | None, float | None, float | None]:
    """Return ``(mean, ci95_half_width, two_tailed_p_vs_zero)``."""
    n = len(values)
    if n == 0:
        return None, None, None
    if n == 1:
        return float(values[0]), None, None
    mean, var = _mean_and_sample_var(values)
    if var <= 0.0:
        p = 1.0 if mean == 0.0 else 0.0
        return mean, 0.0, p
    se = math.sqrt(var / n)
    df = float(n - 1)
    ci95 = _t_critical_95(df) * se
    p_val = student_t_two_tailed_p_value(mean / se, df)
    return mean, ci95, p_val


def welch_t_test(
    values_a: Sequence[float],
    values_b: Sequence[float],
) -> tuple[float | None, float | None, float | None]:
    """Return ``(mean_a - mean_b, t_stat, two_tailed_p_value)`` using Welch's test."""
    n_a = len(values_a)
    n_b = len(values_b)
    if n_a < 2 or n_b < 2:
        return None, None, None
    mean_a, var_a = _mean_and_sample_var(values_a)
    mean_b, var_b = _mean_and_sample_var(values_b)
    diff = mean_a - mean_b
    se_a = var_a / n_a
    se_b = var_b / n_b
    se_sq = se_a + se_b
    if se_sq <= 0.0:
        return diff, None, None
    t_stat = diff / math.sqrt(se_sq)
    denom = (se_a * se_a) / (n_a - 1) + (se_b * se_b) / (n_b - 1)
    if denom <= 0.0:
        return diff, None, None
    df = (se_sq * se_sq) / denom
    return diff, t_stat, student_t_two_tailed_p_value(t_stat, df)


def _significance_label(p_value: float | None, *, is_baseline: bool = False) -> str:
    if is_baseline:
        return "Baseline"
    if p_value is None:
        return "n/a"
    if p_value < 0.001:
        return "p < 0.001 (***)"
    if p_value < 0.01:
        return "p < 0.01 (**)"
    if p_value < 0.05:
        return "p < 0.05 (*)"
    return "Not significant (n.s.)"


def _significance_marker(p_value: float | None, *, is_baseline: bool = False) -> str:
    if is_baseline:
        return "Baseline"
    if p_value is None:
        return ""
    if p_value < 0.001:
        return "***"
    if p_value < 0.01:
        return "**"
    if p_value < 0.05:
        return "*"
    return "n.s."


def build_origin_meta_analysis(comparison: pl.DataFrame) -> pl.DataFrame:
    """Summarize Asian vs European Elo & Win % shifts across 3 regional groups."""
    schema = {
        "origin_group": pl.String,
        "players": pl.Int64,
        "avg_asian_elo": pl.Float64,
        "asian_elo_ci95": pl.Float64,
        "avg_euro_elo": pl.Float64,
        "euro_elo_ci95": pl.Float64,
        "mean_rating_diff": pl.Float64,
        "rating_diff_ci95": pl.Float64,
        "paired_p_value": pl.Float64,
        "mean_win_pct_diff": pl.Float64,
        "win_pct_diff_ci95": pl.Float64,
        "rel_elo_vs_row": pl.Float64,
        "welch_p_vs_row": pl.Float64,
        "rel_win_pct_vs_row": pl.Float64,
        "win_pct_p_vs_row": pl.Float64,
        "significance_label": pl.String,
    }
    if comparison.is_empty():
        return pl.DataFrame(schema=schema)

    grouped = assign_origin_group(comparison)
    by_group: dict[str, pl.DataFrame] = {
        group: grouped.filter(pl.col("origin_group") == group)
        for group in ORIGIN_GROUPS
    }
    row_df = by_group["Rest of World"]
    row_elo_diffs = row_df["rating_diff"].drop_nulls().to_list()
    row_win_diffs = row_df["win_pct_diff"].drop_nulls().to_list()

    rows: list[dict[str, object]] = []
    for group in ORIGIN_GROUPS:
        gdf = by_group[group]
        n = gdf.height
        asian_vals = gdf["asian_rating"].drop_nulls().to_list()
        euro_vals = gdf["european_rating"].drop_nulls().to_list()
        elo_diffs = gdf["rating_diff"].drop_nulls().to_list()
        win_diffs = gdf["win_pct_diff"].drop_nulls().to_list()

        avg_asian, asian_ci, _ = paired_t_test(asian_vals)
        avg_euro, euro_ci, _ = paired_t_test(euro_vals)
        mean_elo_diff, elo_diff_ci, paired_p = paired_t_test(elo_diffs)
        mean_win_diff, win_diff_ci, _ = paired_t_test(win_diffs)

        is_baseline = group == "Rest of World"
        if is_baseline:
            rel_elo = 0.0 if mean_elo_diff is not None else None
            welch_p = None
            rel_win = 0.0 if mean_win_diff is not None else None
            win_p = None
        else:
            rel_elo, _, welch_p = welch_t_test(elo_diffs, row_elo_diffs)
            rel_win, _, win_p = welch_t_test(win_diffs, row_win_diffs)

        rows.append(
            {
                "origin_group": group,
                "players": n,
                "avg_asian_elo": avg_asian,
                "asian_elo_ci95": asian_ci,
                "avg_euro_elo": avg_euro,
                "euro_elo_ci95": euro_ci,
                "mean_rating_diff": mean_elo_diff,
                "rating_diff_ci95": elo_diff_ci,
                "paired_p_value": paired_p,
                "mean_win_pct_diff": mean_win_diff,
                "win_pct_diff_ci95": win_diff_ci,
                "rel_elo_vs_row": rel_elo,
                "welch_p_vs_row": welch_p,
                "rel_win_pct_vs_row": rel_win,
                "win_pct_p_vs_row": win_p,
                "significance_label": _significance_label(
                    welch_p, is_baseline=is_baseline
                ),
            }
        )

    return pl.DataFrame(rows, schema=schema)


def build_origin_avg_elo_chart(meta: pl.DataFrame) -> go.Figure:
    """Build grouped bar chart of average Asian vs European Elo by player region."""
    fig = go.Figure()
    active = meta.filter(pl.col("players") > 0)
    if active.is_empty():
        return style_figure(fig, height=380, legend_bottom=True)

    rows = active.to_dicts()
    x_labels = [f"{r['origin_group']}<br>(n={r['players']})" for r in rows]
    asian_means = [float(r["avg_asian_elo"]) for r in rows]
    asian_cis = [
        float(r["asian_elo_ci95"]) if r["asian_elo_ci95"] is not None else 0.0
        for r in rows
    ]
    euro_means = [float(r["avg_euro_elo"]) for r in rows]
    euro_cis = [
        float(r["euro_elo_ci95"]) if r["euro_elo_ci95"] is not None else 0.0
        for r in rows
    ]

    fig.add_trace(
        go.Bar(
            name="Asian Tables (Avg Elo)",
            x=x_labels,
            y=asian_means,
            error_y={"type": "data", "array": asian_cis, "visible": True},
            marker_color="#F59E0B",
            text=[f"{v:.0f}" for v in asian_means],
            textposition="outside",
            hovertemplate=(
                "%{x}<br>Asian Avg Elo: %{y:.1f} (±%{error_y.array:.1f})<extra></extra>"
            ),
        )
    )
    fig.add_trace(
        go.Bar(
            name="European Tables (Avg Elo)",
            x=x_labels,
            y=euro_means,
            error_y={"type": "data", "array": euro_cis, "visible": True},
            marker_color="#38BDF8",
            text=[f"{v:.0f}" for v in euro_means],
            textposition="outside",
            hovertemplate=(
                "%{x}<br>European Avg Elo: %{y:.1f} (±%{error_y.array:.1f})"
                "<extra></extra>"
            ),
        )
    )

    low_bounds = [
        m - c
        for m, c in zip(asian_means + euro_means, asian_cis + euro_cis, strict=True)
    ]
    high_bounds = [
        m + c
        for m, c in zip(asian_means + euro_means, asian_cis + euro_cis, strict=True)
    ]
    y_min = max(1000.0, math.floor((min(low_bounds) - 80.0) / 50.0) * 50.0)
    y_max = math.ceil((max(high_bounds) + 110.0) / 50.0) * 50.0

    fig.update_layout(
        title="Average Elo: Asian vs European Tables (95% CI)",
        barmode="group",
        yaxis_title="Mean Glicko-2 Rating",
        yaxis={"range": [y_min, y_max]},
    )
    return style_figure(fig, height=390, legend_bottom=True)


def build_origin_edge_chart(meta: pl.DataFrame) -> go.Figure:
    """Build bar chart of mean Asian Table Edge (Elo) with 95% CI and RoW baseline."""
    fig = go.Figure()
    active = meta.filter(pl.col("players") > 0)
    if active.is_empty():
        return style_figure(fig, height=390, legend_bottom=False)

    rows = active.to_dicts()
    x_labels = [f"{r['origin_group']}<br>(n={r['players']})" for r in rows]
    means = [float(r["mean_rating_diff"]) for r in rows]
    cis = [
        float(r["rating_diff_ci95"]) if r["rating_diff_ci95"] is not None else 0.0
        for r in rows
    ]
    colors = [ORIGIN_GROUP_COLORS.get(str(r["origin_group"]), "#94A3B8") for r in rows]
    markers = [
        _significance_marker(
            r["welch_p_vs_row"],
            is_baseline=(r["origin_group"] == "Rest of World"),
        )
        for r in rows
    ]
    text_labels = [
        f"{m:+.1f} ({mk})" if mk else f"{m:+.1f}"
        for m, mk in zip(means, markers, strict=True)
    ]

    fig.add_trace(
        go.Bar(
            name="Mean Asian Table Edge",
            x=x_labels,
            y=means,
            error_y={"type": "data", "array": cis, "visible": True},
            marker_color=colors,
            text=text_labels,
            textposition="outside",
            showlegend=False,
            hovertemplate=(
                "%{x}<br>Mean Elo Diff (Asian \u2212 Euro): %{y:+.1f} "
                "(±%{error_y.array:.1f})<extra></extra>"
            ),
        )
    )

    row_match = [r for r in rows if r["origin_group"] == "Rest of World"]
    if row_match:
        row_mean = float(row_match[0]["mean_rating_diff"])
        fig.add_hline(
            y=row_mean,
            line_dash="dash",
            line_color="#94A3B8",
            annotation_text=f"Rest of World baseline ({row_mean:+.1f})",
            annotation_position="bottom right",
        )
    fig.add_hline(y=0.0, line_width=1, line_color="rgba(128, 128, 128, 0.45)")

    low_bounds = [m - c for m, c in zip(means, cis, strict=True)]
    high_bounds = [m + c for m, c in zip(means, cis, strict=True)]
    pad = max(25.0, 0.25 * max(abs(v) for v in low_bounds + high_bounds + [10.0]))
    fig.update_layout(
        title="Mean Asian Table Edge (Elo) by Region (vs RoW)",
        yaxis_title="Mean Elo Δ (Asian \u2212 European)",
        yaxis={
            "range": [min(0.0, min(low_bounds)) - pad, max(0.0, max(high_bounds)) + pad]
        },
    )
    return style_figure(fig, height=390, legend_bottom=False)

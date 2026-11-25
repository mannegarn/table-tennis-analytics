"""Win-rate aggregations, exact binomial models, logistic S-curve fit, and charts.

This module is pure Python (Polars + NumPy + Plotly) with no Streamlit UI calls
so all calculations and chart builders can be unit-tested directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import comb
from typing import Literal

import numpy as np
import plotly.graph_objects as go  # type: ignore[import-untyped]
import polars as pl
from numpy.typing import NDArray

from .charts import (
    BINOMIAL_CURVE_COLOR,
    HIGHLIGHT_MARKER_COLOR,
    LOGISTIC_CURVE_COLOR,
    STYLE_MARKERS,
    style_figure,
)

# Senior singles games in the warehouse are played to 11 points, win by 2.
SET_TARGET = 11
SET_MARGIN = 2

PlotMode = Literal["point_to_set", "set_to_match", "point_to_match"]

AXIS_METADATA: dict[PlotMode, tuple[str, str, str, str]] = {
    # mode -> (x_col, y_col, x_label, y_label)
    "point_to_set": ("Point %", "Set %", "Point Win Rate (%)", "Set Win Rate (%)"),
    "set_to_match": ("Set %", "Win %", "Set Win Rate (%)", "Match Win Rate (%)"),
    "point_to_match": ("Point %", "Win %", "Point Win Rate (%)", "Match Win Rate (%)"),
}


@dataclass(frozen=True)
class LogisticFitResult:
    """Fitted 2-parameter logistic S-curve strictly bounded in (0%, 100%)."""

    curve_x: NDArray[np.float64]
    curve_y: NDArray[np.float64]
    intercept_a: float
    slope_b: float
    rmse: float


@dataclass(frozen=True)
class PlotSummaryMetrics:
    """Compact summary statistics displayed beneath each Win Rates scatter plot."""

    n_players: int
    n_attackers: int
    n_defenders: int
    binomial_rmse: float | None
    attacker_mean_delta: float | None
    defender_mean_delta: float | None
    logistic_rmse: float | None


def win_set_probability(
    point_prob: NDArray[np.float64] | float,
    *,
    target: int = SET_TARGET,
    margin: int = SET_MARGIN,
) -> NDArray[np.float64]:
    """Return exact set win probability from per-point win probability (with deuce).

    A set ends either:
      1. Straight: reaching ``target`` while the opponent has at most
         ``target - margin`` points (0..9 for an 11-point set).
      2. Deuce: both players reach ``target - 1`` (10-10), followed by a
         win-by-``margin`` geometric race with win probability
         ``p^margin / (p^margin + (1 - p)^margin)``.
    """
    p = np.asarray(point_prob, dtype=np.float64)
    q = 1.0 - p

    straight = np.zeros_like(p, dtype=np.float64)
    for opp_pts in range(target - margin + 1):
        straight = straight + comb(target - 1 + opp_pts, opp_pts) * (p**target) * (
            q**opp_pts
        )

    reach_deuce = comb(2 * (target - 1), target - 1) * ((p * q) ** (target - 1))
    p_m = p**margin
    q_m = q**margin
    denom = p_m + q_m
    win_deuce = np.divide(
        p_m,
        denom,
        out=np.full_like(p, 0.5, dtype=np.float64),
        where=denom > 0.0,
    )
    return np.clip(straight + reach_deuce * win_deuce, 0.0, 1.0)


def binomial_match_probability(
    set_prob: NDArray[np.float64] | float,
    best_of: int,
) -> NDArray[np.float64]:
    """Return exact best-of-N match win probability from per-set win probability."""
    if best_of <= 0 or best_of % 2 == 0:
        msg = f"best_of must be a positive odd integer, got {best_of}"
        raise ValueError(msg)

    s = np.asarray(set_prob, dtype=np.float64)
    q = 1.0 - s
    wins_needed = (best_of + 1) // 2

    total = np.zeros_like(s, dtype=np.float64)
    for wins in range(wins_needed, best_of + 1):
        total = total + comb(best_of, wins) * (s**wins) * (q ** (best_of - wins))
    return np.clip(total, 0.0, 1.0)


def binomial_point_to_match_probability(
    point_prob: NDArray[np.float64] | float,
    best_of: int,
) -> NDArray[np.float64]:
    """Compose Point -> Set (with deuce) and Set -> Match binomial probabilities."""
    set_prob = win_set_probability(point_prob)
    return binomial_match_probability(set_prob, best_of)


def compute_binomial_expected(
    xs_pct: NDArray[np.float64],
    mode: PlotMode,
    best_of: int | None = None,
) -> NDArray[np.float64]:
    """Evaluate the theoretical binomial curve (in 0..100%) for the given plot mode."""
    p = np.clip(np.asarray(xs_pct, dtype=np.float64) / 100.0, 0.0, 1.0)
    if mode == "point_to_set":
        return win_set_probability(p) * 100.0
    if best_of not in (5, 7):
        msg = f"best_of must be 5 or 7 for mode={mode!r}, got {best_of!r}"
        raise ValueError(msg)
    if mode == "set_to_match":
        return binomial_match_probability(p, best_of) * 100.0
    return binomial_point_to_match_probability(p, best_of) * 100.0


def fit_logistic_curve(
    xs_pct: NDArray[np.float64],
    ys_pct: NDArray[np.float64],
    *,
    grid_points: int = 140,
) -> LogisticFitResult | None:
    """Fit a 2-parameter logistic S-curve strictly bounded in (0%, 100%).

    Model: ``y(x) = 100 / (1 + exp(-(a + b * (x - 50) / 100)))``.
    Uses damped Gauss-Newton (Levenberg-Marquardt) in pure NumPy.
    Returns ``None`` when there are fewer than 4 points or zero x-spread.
    """
    xs = np.asarray(xs_pct, dtype=np.float64)
    ys = np.asarray(ys_pct, dtype=np.float64)
    mask = np.isfinite(xs) & np.isfinite(ys)
    xs = xs[mask]
    ys = ys[mask]

    if xs.size < 4 or float(np.ptp(xs)) < 1e-6:
        return None

    z = (xs - 50.0) / 100.0
    t = np.clip(ys / 100.0, 0.0, 1.0)

    a = 0.0
    b = 8.0
    damping = 1e-3

    def _predict_unit(
        a_val: float,
        b_val: float,
        z_arr: NDArray[np.float64],
    ) -> NDArray[np.float64]:
        eta = np.clip(a_val + b_val * z_arr, -25.0, 25.0)
        return np.asarray(1.0 / (1.0 + np.exp(-eta)), dtype=np.float64)

    curr_pred = _predict_unit(a, b, z)
    curr_loss = float(np.mean((curr_pred - t) ** 2))

    for _ in range(40):
        ds = curr_pred * (1.0 - curr_pred)
        resid = curr_pred - t
        jac = np.column_stack([ds, ds * z])
        hess = jac.T @ jac + damping * np.eye(2)
        grad = jac.T @ resid
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            break

        cand_a = a - float(step[0])
        cand_b = b - float(step[1])
        cand_pred = _predict_unit(cand_a, cand_b, z)
        cand_loss = float(np.mean((cand_pred - t) ** 2))

        if cand_loss < curr_loss:
            a, b = cand_a, cand_b
            curr_pred = cand_pred
            if abs(curr_loss - cand_loss) < 1e-11:
                curr_loss = cand_loss
                break
            curr_loss = cand_loss
            damping = max(damping * 0.5, 1e-7)
        else:
            damping = min(damping * 5.0, 1e4)

    rmse = float(np.sqrt(np.mean((ys - curr_pred * 100.0) ** 2)))
    curve_x = np.linspace(
        float(xs.min()),
        float(xs.max()),
        grid_points,
        dtype=np.float64,
    )
    curve_y = _predict_unit(a, b, (curve_x - 50.0) / 100.0) * 100.0

    return LogisticFitResult(
        curve_x=curve_x,
        curve_y=curve_y,
        intercept_a=a,
        slope_b=b,
        rmse=rmse,
    )


def _filter_played_matches(matches: pl.DataFrame) -> pl.DataFrame:
    """Keep completed, played Bo5/Bo7 matches (no walkovers, injuries, or draws)."""
    return matches.filter(
        pl.col("has_result")
        & (~pl.col("is_default_result"))
        & (pl.col("result_type") == "played")
        & pl.col("winner_side").is_not_null()
        & pl.col("best_of").is_in([5, 7])
        & pl.col("p1_player_sk").is_not_null()
        & pl.col("p2_player_sk").is_not_null()
    )


def _clean_text_attr(col_name: str, alias: str) -> pl.Expr:
    cleaned = pl.col(col_name).cast(pl.String).str.strip_chars()
    return (
        pl.when(cleaned.is_null() | (cleaned == ""))
        .then(pl.lit("Unknown"))
        .otherwise(cleaned)
        .alias(alias)
    )


def _normalize_players(players: pl.DataFrame) -> pl.DataFrame:
    """Normalize player profile fields for clean filtering and chart labels."""
    return players.select(
        pl.col("player_sk"),
        pl.col("player_name").fill_null("Unknown Player").alias("Player"),
        pl.col("organization_code").fill_null("").alias("Organization"),
        pl.when(pl.col("gender") == "M")
        .then(pl.lit("Men"))
        .when(pl.col("gender") == "F")
        .then(pl.lit("Women"))
        .otherwise(pl.lit("Unknown"))
        .alias("Gender"),
        _clean_text_attr("style", "Style"),
        _clean_text_attr("grip", "Grip"),
        _clean_text_attr("hand", "Hand"),
    )


def _unpivot_match_appearances(played: pl.DataFrame) -> pl.DataFrame:
    """Expand each match into two player-appearance rows (p1 and p2)."""
    return pl.concat(
        [
            played.select(
                pl.col("p1_player_sk").alias("player_sk"),
                pl.col("best_of").cast(pl.Int64).alias("Best Of"),
                (pl.col("winner_side") == 1).cast(pl.UInt32).alias("won"),
            ),
            played.select(
                pl.col("p2_player_sk").alias("player_sk"),
                pl.col("best_of").cast(pl.Int64).alias("Best Of"),
                (pl.col("winner_side") == 2).cast(pl.UInt32).alias("won"),
            ),
        ]
    )


def _unpivot_game_appearances(
    played: pl.DataFrame,
    games: pl.DataFrame,
) -> pl.DataFrame:
    """Join valid 11-point sets to played matches and expand into p1/p2 rows."""
    valid_games = games.filter(pl.col("is_valid_game_score")).join(
        played.select("match_sk", "p1_player_sk", "p2_player_sk", "best_of"),
        on="match_sk",
        how="inner",
    )
    return pl.concat(
        [
            valid_games.select(
                pl.col("p1_player_sk").alias("player_sk"),
                pl.col("best_of").cast(pl.Int64).alias("Best Of"),
                (pl.col("winner_side") == 1).cast(pl.UInt32).alias("set_won"),
                pl.col("p1_points").cast(pl.Int64).alias("points_won"),
                (pl.col("p1_points") + pl.col("p2_points"))
                .cast(pl.Int64)
                .alias("points_played"),
            ),
            valid_games.select(
                pl.col("p2_player_sk").alias("player_sk"),
                pl.col("best_of").cast(pl.Int64).alias("Best Of"),
                (pl.col("winner_side") == 2).cast(pl.UInt32).alias("set_won"),
                pl.col("p2_points").cast(pl.Int64).alias("points_won"),
                (pl.col("p1_points") + pl.col("p2_points"))
                .cast(pl.Int64)
                .alias("points_played"),
            ),
        ]
    )


def build_combined_point_set_stats(
    matches: pl.DataFrame,
    games: pl.DataFrame,
    players: pl.DataFrame,
) -> pl.DataFrame:
    """Aggregate Point % and Set % across Bo5 + Bo7 combined (for Point vs Set).

    Every senior singles set is played to 11 points (win by 2) regardless of
    whether the match is Best of 5 or Best of 7, so pooling formats is exact
    for the Point % -> Set % relationship.
    """
    played = _filter_played_matches(matches)
    match_counts = (
        _unpivot_match_appearances(played)
        .group_by("player_sk")
        .agg(pl.len().cast(pl.Int64).alias("Matches"))
    )
    set_stats = (
        _unpivot_game_appearances(played, games)
        .group_by("player_sk")
        .agg(
            pl.len().cast(pl.Int64).alias("Sets Played"),
            pl.col("set_won").sum().cast(pl.Int64).alias("Sets Won"),
            pl.col("points_won").sum().alias("Points Won"),
            pl.col("points_played").sum().alias("Points Played"),
        )
    )
    norm_players = _normalize_players(players)

    return (
        match_counts.join(set_stats, on="player_sk", how="inner")
        .join(norm_players, on="player_sk", how="inner")
        .filter((pl.col("Sets Played") > 0) & (pl.col("Points Played") > 0))
        .with_columns(
            (100.0 * pl.col("Sets Won") / pl.col("Sets Played"))
            .round(2)
            .alias("Set %"),
            (100.0 * pl.col("Points Won") / pl.col("Points Played"))
            .round(2)
            .alias("Point %"),
        )
        .select(
            "player_sk",
            "Player",
            "Organization",
            "Gender",
            "Style",
            "Grip",
            "Hand",
            "Matches",
            "Sets Played",
            "Sets Won",
            "Set %",
            "Points Played",
            "Points Won",
            "Point %",
        )
        .sort(["Matches", "Set %"], descending=True)
    )


def build_format_win_rates(
    matches: pl.DataFrame,
    games: pl.DataFrame,
    players: pl.DataFrame,
) -> pl.DataFrame:
    """Aggregate Match %, Set %, and Point % strictly per (player, best_of).

    Keeps Best of 5 and Best of 7 completely separate so match-level plots
    never pool formats with different theoretical binomial curves.
    """
    played = _filter_played_matches(matches)
    match_stats = (
        _unpivot_match_appearances(played)
        .group_by(["player_sk", "Best Of"])
        .agg(
            pl.len().cast(pl.Int64).alias("Matches"),
            pl.col("won").sum().cast(pl.Int64).alias("Wins"),
        )
    )
    set_stats = (
        _unpivot_game_appearances(played, games)
        .group_by(["player_sk", "Best Of"])
        .agg(
            pl.len().cast(pl.Int64).alias("Sets Played"),
            pl.col("set_won").sum().cast(pl.Int64).alias("Sets Won"),
            pl.col("points_won").sum().alias("Points Won"),
            pl.col("points_played").sum().alias("Points Played"),
        )
    )
    norm_players = _normalize_players(players)

    return (
        match_stats.join(set_stats, on=["player_sk", "Best Of"], how="inner")
        .join(norm_players, on="player_sk", how="inner")
        .filter(
            (pl.col("Matches") > 0)
            & (pl.col("Sets Played") > 0)
            & (pl.col("Points Played") > 0)
        )
        .with_columns(
            (pl.col("Matches") - pl.col("Wins")).alias("Losses"),
            (100.0 * pl.col("Wins") / pl.col("Matches")).round(2).alias("Win %"),
            (100.0 * pl.col("Sets Won") / pl.col("Sets Played"))
            .round(2)
            .alias("Set %"),
            (100.0 * pl.col("Points Won") / pl.col("Points Played"))
            .round(2)
            .alias("Point %"),
        )
        .select(
            "player_sk",
            "Best Of",
            "Player",
            "Organization",
            "Gender",
            "Style",
            "Grip",
            "Hand",
            "Matches",
            "Wins",
            "Losses",
            "Win %",
            "Sets Played",
            "Sets Won",
            "Set %",
            "Points Played",
            "Points Won",
            "Point %",
        )
        .sort(["Best Of", "Matches", "Win %"], descending=[False, True, True])
    )


def filter_win_rate_rows(
    df: pl.DataFrame,
    *,
    gender: str = "All",
    min_matches: int = 10,
    best_of: int | None = None,
    styles: Sequence[str] | None = None,
    grips: Sequence[str] | None = None,
    hands: Sequence[str] | None = None,
) -> pl.DataFrame:
    """Filter player win-rate rows by gender, format, minimum matches, and profile."""
    filtered = df.filter(pl.col("Matches") >= min_matches)
    if best_of is not None and "Best Of" in filtered.columns:
        filtered = filtered.filter(pl.col("Best Of") == best_of)
    if gender != "All":
        filtered = filtered.filter(pl.col("Gender") == gender)
    if styles is not None:
        filtered = filtered.filter(pl.col("Style").is_in(list(styles)))
    if grips is not None:
        filtered = filtered.filter(pl.col("Grip").is_in(list(grips)))
    if hands is not None:
        filtered = filtered.filter(pl.col("Hand").is_in(list(hands)))
    return filtered


def _axis_range(values: NDArray[np.float64], pad: float = 3.0) -> list[float]:
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


def _binomial_curve_label(mode: PlotMode, best_of: int | None) -> str:
    if mode == "point_to_set":
        return "Binomial model (11-pt set w/ deuce)"
    if mode == "set_to_match":
        return f"Binomial model (Best of {best_of})"
    return f"Binomial model (11-pt → Bo{best_of})"


def build_win_rate_scatter(
    df: pl.DataFrame,
    *,
    mode: PlotMode,
    title: str,
    best_of: int | None = None,
    gender_label: str = "All",
    show_logistic_fit: bool = False,
    highlight_player: str | None = None,
    height: int = 470,
) -> tuple[go.Figure, PlotSummaryMetrics]:
    """Build a polished, theme-aware Win Rates scatter plot with binomial baseline."""
    x_col, y_col, x_label, y_label = AXIS_METADATA[mode]

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
        empty_metrics = PlotSummaryMetrics(
            n_players=0,
            n_attackers=0,
            n_defenders=0,
            binomial_rmse=None,
            attacker_mean_delta=None,
            defender_mean_delta=None,
            logistic_rmse=None,
        )
        return empty_fig, empty_metrics

    xs = clean_df[x_col].to_numpy().astype(np.float64)
    ys = clean_df[y_col].to_numpy().astype(np.float64)
    expected = compute_binomial_expected(xs, mode=mode, best_of=best_of)
    deltas = ys - expected

    enriched = clean_df.with_columns(
        pl.Series("_expected_y", np.round(expected, 2)),
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
                subset["_expected_y"].to_list(),
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
                    "Matches: %{customdata[3]}<br>"
                    f"{x_label}: %{{x:.1f}}%<br>"
                    f"{y_label}: %{{y:.1f}}%<br>"
                    "Binomial expected: %{customdata[4]:.1f}% "
                    "(%{customdata[5]:+.1f} pp)<extra></extra>"
                ),
            )
        )

    # 2. Exact Binomial reference curve over observed x-domain (padded slightly)
    x_range = _axis_range(xs, pad=2.5)
    curve_x = np.linspace(x_range[0], x_range[1], 180, dtype=np.float64)
    curve_y = compute_binomial_expected(curve_x, mode=mode, best_of=best_of)
    binom_label = _binomial_curve_label(mode, best_of)

    fig.add_trace(
        go.Scatter(
            x=curve_x.tolist(),
            y=curve_y.tolist(),
            mode="lines",
            name=binom_label,
            line={"color": BINOMIAL_CURVE_COLOR, "width": 2.8},
            hovertemplate=(
                f"<b>{binom_label}</b><br>"
                f"{x_label}: %{{x:.1f}}%<br>"
                f"Expected {y_label}: %{{y:.1f}}%<extra></extra>"
            ),
        )
    )

    # 3. Optional 2-parameter Logistic S-curve empirical fit
    logistic_rmse: float | None = None
    if show_logistic_fit:
        fit = fit_logistic_curve(xs, ys)
        if fit is not None:
            logistic_rmse = fit.rmse
            fig.add_trace(
                go.Scatter(
                    x=fit.curve_x.tolist(),
                    y=fit.curve_y.tolist(),
                    mode="lines",
                    name="Empirical logistic S-curve",
                    line={
                        "color": LOGISTIC_CURVE_COLOR,
                        "width": 2.3,
                        "dash": "dash",
                    },
                    hovertemplate=(
                        "<b>Empirical logistic S-curve</b><br>"
                        f"{x_label}: %{{x:.1f}}%<br>"
                        f"Fitted {y_label}: %{{y:.1f}}%<extra></extra>"
                    ),
                )
            )

    # 4. Optional highlighted player star marker
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
                        f"Matches: {row['Matches']}<br>"
                        f"{x_label}: %{{x:.1f}}%<br>"
                        f"{y_label}: %{{y:.1f}}%<br>"
                        f"Binomial expected: {row['_expected_y']:.1f}% "
                        f"({row['_delta_pp']:+.1f} pp)<extra></extra>"
                    ),
                )
            )

    n_attackers = enriched.filter(pl.col("Style") == "Attacker").height
    n_defenders = enriched.filter(pl.col("Style") == "Defender").height

    scope_parts = [f"n = {enriched.height:,} players"]
    if best_of is not None:
        scope_parts.append(f"Best of {best_of}")
    else:
        scope_parts.append("Bo5 + Bo7 combined")
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
        margin={"l": 52, "r": 18, "t": 68, "b": 108},
    )

    y_all = np.concatenate([ys, curve_y])
    y_range = _axis_range(y_all, pad=3.5)

    # Subtle 50% break-even crosshairs anchor the S-curve center visually
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

    fig.update_xaxes(
        title_text=x_label,
        range=x_range,
        ticksuffix="%",
    )
    fig.update_yaxes(
        title_text=y_label,
        range=y_range,
        ticksuffix="%",
    )

    binom_rmse = float(np.sqrt(np.mean(deltas**2)))
    att_deltas = enriched.filter(pl.col("Style") == "Attacker")["_delta_pp"]
    def_deltas = enriched.filter(pl.col("Style") == "Defender")["_delta_pp"]

    att_mean = float(att_deltas.mean()) if not att_deltas.is_empty() else None  # type: ignore[arg-type]
    def_mean = float(def_deltas.mean()) if not def_deltas.is_empty() else None  # type: ignore[arg-type]

    metrics = PlotSummaryMetrics(
        n_players=enriched.height,
        n_attackers=n_attackers,
        n_defenders=n_defenders,
        binomial_rmse=binom_rmse,
        attacker_mean_delta=att_mean,
        defender_mean_delta=def_mean,
        logistic_rmse=logistic_rmse,
    )
    return fig, metrics


def build_delta_extremes(
    df: pl.DataFrame,
    *,
    mode: PlotMode,
    best_of: int | None = None,
    top_n: int = 5,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Return top-N players furthest above and below the theoretical binomial curve."""
    x_col, y_col, _, _ = AXIS_METADATA[mode]
    clean_df = df.filter(
        pl.col(x_col).is_not_null()
        & pl.col(y_col).is_not_null()
        & pl.col(x_col).is_finite()
        & pl.col(y_col).is_finite()
    )
    if clean_df.is_empty():
        empty = pl.DataFrame()
        return empty, empty

    xs = clean_df[x_col].to_numpy().astype(np.float64)
    ys = clean_df[y_col].to_numpy().astype(np.float64)
    expected = compute_binomial_expected(xs, mode=mode, best_of=best_of)
    deltas = ys - expected

    scored = clean_df.with_columns(
        pl.Series("Binomial %", np.round(expected, 1)),
        pl.Series("Delta (pp)", np.round(deltas, 1)),
    ).select(
        "Player",
        pl.col("Organization").alias("Org"),
        "Style",
        "Matches",
        pl.col(x_col).round(1),
        pl.col(y_col).round(1),
        "Binomial %",
        "Delta (pp)",
    )

    above = scored.sort(["Delta (pp)", "Matches"], descending=[True, True]).head(top_n)
    below = scored.sort(["Delta (pp)", "Matches"], descending=[False, True]).head(top_n)
    return above, below

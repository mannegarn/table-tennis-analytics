"""Glicko-2 upset ranking, prediction accuracy, and calibration analytics.

Pure Python (Polars + Plotly) with no Streamlit UI calls so all calculations
and chart builders can be unit-tested directly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import plotly.graph_objects as go  # type: ignore[import-untyped]
import polars as pl

from .charts import BINOMIAL_CURVE_COLOR, GENDER_COLORS, style_figure
from .glicko import flag_url_for_code

RankBy = Literal["loser_prob", "rating_diff"]


@dataclass(frozen=True)
class AccuracyMetrics:
    """Summary of Glicko-2 pre-match winner prediction accuracy."""

    decisive_matches: int
    correct_predictions: int
    upsets: int
    accuracy_pct: float | None
    upset_rate_pct: float | None


def attach_prior_match_counts(ratings: pl.DataFrame) -> pl.DataFrame:
    """Attach chronological prior rated match counts per player onto each match row."""
    if ratings.is_empty():
        return ratings.with_columns(
            pl.lit(0, dtype=pl.Int64).alias("p1_prior_matches"),
            pl.lit(0, dtype=pl.Int64).alias("p2_prior_matches"),
        )

    appearances = pl.concat(
        [
            ratings.select(
                pl.col("match_sk").cast(pl.Int64),
                pl.col("gender_category").cast(pl.String),
                pl.col("rating_date").cast(pl.Date),
                pl.col("p1_player_sk").cast(pl.Int64).alias("player_sk"),
                pl.lit(1, dtype=pl.Int8).alias("_side"),
            ),
            ratings.select(
                pl.col("match_sk").cast(pl.Int64),
                pl.col("gender_category").cast(pl.String),
                pl.col("rating_date").cast(pl.Date),
                pl.col("p2_player_sk").cast(pl.Int64).alias("player_sk"),
                pl.lit(2, dtype=pl.Int8).alias("_side"),
            ),
        ],
        how="vertical",
    ).sort(["rating_date", "match_sk"], nulls_last=False)

    counted = appearances.with_columns(
        pl.int_range(0, pl.len(), dtype=pl.Int64)
        .over(["gender_category", "player_sk"])
        .alias("prior_matches")
    )

    p1_prior = counted.filter(pl.col("_side") == 1).select(
        "match_sk",
        pl.col("prior_matches").alias("p1_prior_matches"),
    )
    p2_prior = counted.filter(pl.col("_side") == 2).select(
        "match_sk",
        pl.col("prior_matches").alias("p2_prior_matches"),
    )

    return ratings.join(p1_prior, on="match_sk", how="left").join(
        p2_prior, on="match_sk", how="left"
    )


def _decisive_matches(
    ratings_with_prior: pl.DataFrame,
    *,
    min_prior_matches: int = 0,
    gender: Literal["men", "women"] | None = None,
) -> pl.DataFrame:
    df = ratings_with_prior.filter(
        (pl.col("p1_actual_score") != 0.5)
        & (pl.col("p1_expected_win_prob") != 0.5)
        & (pl.col("p1_prior_matches") >= min_prior_matches)
        & (pl.col("p2_prior_matches") >= min_prior_matches)
    )
    if gender is not None:
        df = df.filter(pl.col("gender_category") == gender)
    return df


def compute_prediction_accuracy(
    ratings_with_prior: pl.DataFrame,
    *,
    min_prior_matches: int = 0,
) -> dict[str, AccuracyMetrics]:
    """Compute prediction accuracy and upset rates for Men's and Women's pools."""
    results: dict[str, AccuracyMetrics] = {}
    genders: tuple[Literal["men", "women"], ...] = ("men", "women")

    for gender in genders:
        sub = _decisive_matches(
            ratings_with_prior,
            min_prior_matches=min_prior_matches,
            gender=gender,
        )
        total = sub.height
        if total == 0:
            results[gender] = AccuracyMetrics(
                decisive_matches=0,
                correct_predictions=0,
                upsets=0,
                accuracy_pct=None,
                upset_rate_pct=None,
            )
            continue

        correct = sub.filter(
            (
                (pl.col("p1_expected_win_prob") > 0.5)
                & (pl.col("p1_actual_score") == 1.0)
            )
            | (
                (pl.col("p1_expected_win_prob") < 0.5)
                & (pl.col("p1_actual_score") == 0.0)
            )
        ).height
        upsets = total - correct
        accuracy_pct = 100.0 * correct / total
        results[gender] = AccuracyMetrics(
            decisive_matches=total,
            correct_predictions=correct,
            upsets=upsets,
            accuracy_pct=accuracy_pct,
            upset_rate_pct=100.0 - accuracy_pct,
        )

    return results


def _empty_upsets_table() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "rank": pl.Int64,
            "match_sk": pl.Int64,
            "event_sk": pl.Int64,
            "rating_date": pl.Date,
            "event_name": pl.String,
            "round_code": pl.String,
            "winner_player_sk": pl.Int64,
            "winner_flag_url": pl.String,
            "winner_org": pl.String,
            "winner_name": pl.String,
            "winner_pre_rating": pl.Float64,
            "score": pl.String,
            "loser_player_sk": pl.Int64,
            "loser_flag_url": pl.String,
            "loser_org": pl.String,
            "loser_name": pl.String,
            "loser_pre_rating": pl.Float64,
            "rating_diff": pl.Float64,
            "loser_win_prob_pct": pl.Float64,
            "winner_win_prob_pct": pl.Float64,
            "winner_rating_delta": pl.Float64,
        }
    )


def build_upsets_table(
    ratings_with_prior: pl.DataFrame,
    players: pl.DataFrame,
    events: pl.DataFrame,
    matches: pl.DataFrame,
    *,
    gender: Literal["men", "women"],
    min_prior_matches: int = 10,
    rank_by: RankBy = "loser_prob",
    year: int | None = None,
) -> pl.DataFrame:
    """Build ranked list of match upsets where the pre-match underdog won."""
    decisive = _decisive_matches(
        ratings_with_prior,
        min_prior_matches=min_prior_matches,
        gender=gender,
    )
    if year is not None:
        decisive = decisive.filter(
            pl.col("rating_date").is_not_null()
            & (pl.col("rating_date").dt.year() == year)
        )
    if decisive.is_empty():
        return _empty_upsets_table()

    oriented = decisive.with_columns(
        (pl.col("p1_actual_score") == 1.0).alias("_p1_won"),
    ).with_columns(
        pl.when(pl.col("_p1_won"))
        .then(pl.col("p1_player_sk"))
        .otherwise(pl.col("p2_player_sk"))
        .cast(pl.Int64)
        .alias("winner_player_sk"),
        pl.when(pl.col("_p1_won"))
        .then(pl.col("p2_player_sk"))
        .otherwise(pl.col("p1_player_sk"))
        .cast(pl.Int64)
        .alias("loser_player_sk"),
        pl.when(pl.col("_p1_won"))
        .then(pl.col("p1_pre_rating"))
        .otherwise(pl.col("p2_pre_rating"))
        .cast(pl.Float64)
        .alias("winner_pre_rating"),
        pl.when(pl.col("_p1_won"))
        .then(pl.col("p2_pre_rating"))
        .otherwise(pl.col("p1_pre_rating"))
        .cast(pl.Float64)
        .alias("loser_pre_rating"),
        pl.when(pl.col("_p1_won"))
        .then(pl.col("p1_post_rating") - pl.col("p1_pre_rating"))
        .otherwise(pl.col("p2_post_rating") - pl.col("p2_pre_rating"))
        .cast(pl.Float64)
        .alias("winner_rating_delta"),
        pl.when(pl.col("_p1_won"))
        .then(pl.col("p1_expected_win_prob"))
        .otherwise(1.0 - pl.col("p1_expected_win_prob"))
        .cast(pl.Float64)
        .alias("_winner_win_prob"),
    )

    upsets = oriented.filter(pl.col("_winner_win_prob") < 0.5).with_columns(
        (pl.col("loser_pre_rating") - pl.col("winner_pre_rating")).alias("rating_diff"),
        (100.0 * (1.0 - pl.col("_winner_win_prob"))).alias("loser_win_prob_pct"),
        (100.0 * pl.col("_winner_win_prob")).alias("winner_win_prob_pct"),
    )
    if upsets.is_empty():
        return _empty_upsets_table()

    player_lookup = players.select(
        pl.col("player_sk").cast(pl.Int64),
        pl.col("player_name").fill_null("Unknown Player").cast(pl.String),
        pl.coalesce([pl.col("organization_code"), pl.col("country_code")])
        .fill_null("")
        .cast(pl.String)
        .str.strip_chars()
        .alias("_org"),
    )
    event_lookup = events.select(
        pl.col("event_sk").cast(pl.Int64),
        pl.col("event_name").fill_null("Unknown Event").cast(pl.String),
    )
    match_lookup = matches.select(
        pl.col("match_sk").cast(pl.Int64),
        pl.col("round_code").cast(pl.String),
        pl.col("games_won_p1").cast(pl.Int64),
        pl.col("games_won_p2").cast(pl.Int64),
    )

    enriched = (
        upsets.join(
            player_lookup.select(
                pl.col("player_sk").alias("winner_player_sk"),
                pl.col("player_name").alias("winner_name"),
                pl.col("_org").alias("winner_org"),
            ),
            on="winner_player_sk",
            how="left",
        )
        .join(
            player_lookup.select(
                pl.col("player_sk").alias("loser_player_sk"),
                pl.col("player_name").alias("loser_name"),
                pl.col("_org").alias("loser_org"),
            ),
            on="loser_player_sk",
            how="left",
        )
        .join(event_lookup, on="event_sk", how="left")
        .join(match_lookup, on="match_sk", how="left")
        .with_columns(
            pl.col("winner_name").fill_null("Unknown Player"),
            pl.col("loser_name").fill_null("Unknown Player"),
            pl.col("winner_org").fill_null(""),
            pl.col("loser_org").fill_null(""),
            pl.col("event_name").fill_null("Unknown Event"),
            pl.when(
                pl.col("games_won_p1").is_not_null()
                & pl.col("games_won_p2").is_not_null()
            )
            .then(
                pl.when(pl.col("_p1_won"))
                .then(
                    pl.col("games_won_p1").cast(pl.String)
                    + pl.lit("-")
                    + pl.col("games_won_p2").cast(pl.String)
                )
                .otherwise(
                    pl.col("games_won_p2").cast(pl.String)
                    + pl.lit("-")
                    + pl.col("games_won_p1").cast(pl.String)
                )
            )
            .otherwise(pl.lit(None, dtype=pl.String))
            .alias("score"),
        )
    )

    sort_cols = (
        ["loser_win_prob_pct", "rating_diff", "rating_date", "match_sk"]
        if rank_by == "loser_prob"
        else ["rating_diff", "loser_win_prob_pct", "rating_date", "match_sk"]
    )
    ranked = enriched.sort(
        sort_cols,
        descending=[True, True, True, True],
        nulls_last=True,
    ).with_columns(pl.int_range(1, pl.len() + 1, dtype=pl.Int64).alias("rank"))

    winner_flags = [flag_url_for_code(c) for c in ranked["winner_org"].to_list()]
    loser_flags = [flag_url_for_code(c) for c in ranked["loser_org"].to_list()]

    return ranked.with_columns(
        pl.Series("winner_flag_url", winner_flags, dtype=pl.String),
        pl.Series("loser_flag_url", loser_flags, dtype=pl.String),
    ).select(
        "rank",
        "match_sk",
        "event_sk",
        "rating_date",
        "event_name",
        "round_code",
        "winner_player_sk",
        "winner_flag_url",
        "winner_org",
        "winner_name",
        "winner_pre_rating",
        "score",
        "loser_player_sk",
        "loser_flag_url",
        "loser_org",
        "loser_name",
        "loser_pre_rating",
        "rating_diff",
        "loser_win_prob_pct",
        "winner_win_prob_pct",
        "winner_rating_delta",
    )


def build_calibration_buckets(
    ratings_with_prior: pl.DataFrame,
    *,
    gender: Literal["men", "women"],
    min_prior_matches: int = 0,
) -> pl.DataFrame:
    """Aggregate expected vs actual favorite win rates into 1% probability buckets."""
    decisive = _decisive_matches(
        ratings_with_prior,
        min_prior_matches=min_prior_matches,
        gender=gender,
    )
    if decisive.is_empty():
        return pl.DataFrame(
            schema={
                "prob_bucket": pl.Int64,
                "matches": pl.Int64,
                "expected_win_pct": pl.Float64,
                "actual_win_pct": pl.Float64,
                "delta_pp": pl.Float64,
            }
        )

    fav_df = decisive.with_columns(
        pl.when(pl.col("p1_expected_win_prob") > 0.5)
        .then(pl.col("p1_expected_win_prob"))
        .otherwise(1.0 - pl.col("p1_expected_win_prob"))
        .alias("_fav_prob"),
        pl.when(
            (
                (pl.col("p1_expected_win_prob") > 0.5)
                & (pl.col("p1_actual_score") == 1.0)
            )
            | (
                (pl.col("p1_expected_win_prob") < 0.5)
                & (pl.col("p1_actual_score") == 0.0)
            )
        )
        .then(1.0)
        .otherwise(0.0)
        .alias("_fav_won"),
    ).with_columns(
        (pl.col("_fav_prob") * 100.0)
        .floor()
        .cast(pl.Int64)
        .clip(50, 99)
        .alias("prob_bucket")
    )

    return (
        fav_df.group_by("prob_bucket")
        .agg(
            pl.len().cast(pl.Int64).alias("matches"),
            (100.0 * pl.col("_fav_prob").mean()).alias("expected_win_pct"),
            (100.0 * pl.col("_fav_won").mean()).alias("actual_win_pct"),
        )
        .with_columns(
            (pl.col("actual_win_pct") - pl.col("expected_win_pct")).alias("delta_pp")
        )
        .sort("prob_bucket")
    )


def build_calibration_chart(
    buckets: pl.DataFrame,
    *,
    gender_label: str,
) -> go.Figure:
    """Plot 1% favorite win-probability buckets against the ideal y = x line."""
    fig = go.Figure()
    if buckets.is_empty():
        return style_figure(fig, height=380, legend_bottom=True)

    bucket_starts = buckets["prob_bucket"].to_list()
    expected = buckets["expected_win_pct"].to_list()
    actual = buckets["actual_win_pct"].to_list()
    counts = buckets["matches"].to_list()
    deltas = buckets["delta_pp"].to_list()
    marker_color = GENDER_COLORS.get(gender_label, GENDER_COLORS["Men"])

    sizes = [min(18.0, max(6.0, 6.0 + math.sqrt(c) * 0.65)) for c in counts]
    customdata = [
        (b, b + 1, c, d) for b, c, d in zip(bucket_starts, counts, deltas, strict=True)
    ]

    fig.add_trace(
        go.Scatter(
            x=[50.0, 100.0],
            y=[50.0, 100.0],
            mode="lines",
            name="Perfect calibration (Expected = Actual)",
            line={"color": BINOMIAL_CURVE_COLOR, "width": 2.2, "dash": "dash"},
            hoverinfo="skip",
        )
    )

    fig.add_trace(
        go.Scatter(
            x=expected,
            y=actual,
            mode="markers",
            name=f"1% Buckets ({gender_label})",
            marker={
                "color": marker_color,
                "size": sizes,
                "opacity": 0.85,
                "line": {"width": 1.0, "color": "rgba(255,255,255,0.45)"},
            },
            customdata=customdata,
            hovertemplate=(
                "<b>Bucket %{customdata[0]}%-%{customdata[1]}%</b><br>"
                "Matches: %{customdata[2]:,}<br>"
                "Expected favorite win: %{x:.1f}%<br>"
                "Actual favorite win: %{y:.1f}%<br>"
                "Diff: %{customdata[3]:+.1f} pp<extra></extra>"
            ),
        )
    )

    fig.update_xaxes(
        title_text="Glicko-2 Pre-Match Favorite Win Chance (1% buckets)",
        range=[49, 101],
        ticksuffix="%",
    )
    fig.update_yaxes(
        title_text="Actual Favorite Win Rate",
        range=[45, 103],
        ticksuffix="%",
    )
    return style_figure(fig, height=380, legend_bottom=True)

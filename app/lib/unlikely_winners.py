"""Tournament-level Glicko-2 unlikely winners analytics and chart builder.

Works backwards from each non-team singles tournament Final winner and multiplies
their pre-match Glicko-2 expected win probabilities across qualifying and knockout
rounds (excluding group, placement, and consolation matches).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import plotly.graph_objects as go  # type: ignore[import-untyped]
import polars as pl

from .charts import GENDER_COLORS, style_figure
from .glicko import (
    flag_url_for_code,
    resolve_placeholder_data_uri,
    sanitize_headshot_url,
)
from .upsets import attach_prior_match_counts

ELIGIBLE_ROUND_STAGES: tuple[str, ...] = ("knockout", "qualifying")

TOURNAMENT_TYPE_OPTIONS: tuple[str, ...] = (
    "Olympics",
    "World Championships (WTTC)",
    "World Cup",
    "WTT Grand Smash",
    "WTT Finals",
    "WTT Champions",
    "WTT Star Contender",
    "WTT Contender",
    "WTT Feeder",
    "Continental",
    "Other / Open",
)

_EVENT_TYPE_TO_TOURNAMENT_TYPE: dict[str, str] = {
    "Olympic Games": "Olympics",
    "WTTC": "World Championships (WTTC)",
    "Singles World Cup": "World Cup",
    "WTT Grand Smash": "WTT Grand Smash",
    "WTT Finals": "WTT Finals",
    "WTT Cup Finals": "WTT Finals",
    "WTT Champions": "WTT Champions",
    "WTT Star Contender": "WTT Star Contender",
    "WTT Contender": "WTT Contender",
    "WTT Feeder": "WTT Feeder",
    "Continental Championships": "Continental",
    "Continental Cups": "Continental",
    "Continental": "Continental",
}


def map_tournament_type(event_type: str | None) -> str:
    """Map raw ``dim_events.event_type`` to a clean tournament-type category."""
    if not event_type:
        return "Other / Open"
    return _EVENT_TYPE_TO_TOURNAMENT_TYPE.get(event_type.strip(), "Other / Open")


def format_win_prob_pct(prob_pct: float) -> str:
    """Format a tournament win probability percentage for compact UI display."""
    if prob_pct < 0.01:
        return "<0.01%"
    if prob_pct < 10.0:
        return f"{prob_pct:.2f}%"
    return f"{prob_pct:.1f}%"


def _empty_unlikely_winners_table() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "rank": pl.Int64,
            "event_sk": pl.Int64,
            "gender_category": pl.String,
            "event_name": pl.String,
            "event_year": pl.Int64,
            "event_type": pl.String,
            "tournament_type": pl.String,
            "final_date": pl.Date,
            "final_match_sk": pl.Int64,
            "winner_player_sk": pl.Int64,
            "winner_name": pl.String,
            "winner_org": pl.String,
            "winner_flag_url": pl.String,
            "winner_photo_url": pl.String,
            "final_score": pl.String,
            "loser_player_sk": pl.Int64,
            "loser_name": pl.String,
            "loser_org": pl.String,
            "loser_flag_url": pl.String,
            "champ_pre_rating": pl.Float64,
            "champ_start_prior_matches": pl.Int64,
            "wins_count": pl.Int64,
            "rated_wins": pl.Int64,
            "round_path": pl.String,
            "tournament_win_prob_pct": pl.Float64,
        }
    )


def build_unlikely_winners_table(
    matches: pl.DataFrame,
    ratings_with_prior: pl.DataFrame,
    players: pl.DataFrame,
    events: pl.DataFrame,
    *,
    min_prior_matches: int = 10,
    tournament_types: Sequence[str] | None = None,
    year: int | None = None,
    theme: Literal["dark", "light"] = "dark",
    placeholders_dir: Path | None = None,
) -> pl.DataFrame:
    """Rank singles tournament champions from lowest to highest Glicko-2 win chance.

    Excludes team rubbers (``is_team_rubber = True``) and non-elimination stages
    (group, placement, consolation), including only ``knockout`` and ``qualifying``
    wins along each champion's winning path to the Final (``round_code == 'F'``).
    Unrated wins (e.g. walkover/injury defaults) count as ``1.0`` in the product.
    """
    if matches.is_empty() or ratings_with_prior.is_empty():
        return _empty_unlikely_winners_table()

    if (
        "p1_prior_matches" not in ratings_with_prior.columns
        or "p2_prior_matches" not in ratings_with_prior.columns
    ):
        ratings_with_prior = attach_prior_match_counts(ratings_with_prior)

    scoped = matches.filter(
        pl.col("gender_category").is_in(["men", "women"])
        & pl.col("round_stage").is_in(ELIGIBLE_ROUND_STAGES)
    )
    if "is_team_rubber" in scoped.columns:
        scoped = scoped.filter(~pl.col("is_team_rubber").fill_null(False))

    if scoped.is_empty():
        return _empty_unlikely_winners_table()

    finals_raw = scoped.filter(
        (pl.col("round_code") == "F") & pl.col("winner_player_sk").is_not_null()
    )
    if finals_raw.is_empty():
        return _empty_unlikely_winners_table()

    finals_dedup = finals_raw.sort("match_sk", descending=True).unique(
        subset=["event_sk", "gender_category"],
        keep="first",
    )

    if "result_type" in finals_dedup.columns:
        fallback_score_expr = (
            pl.when(pl.col("result_type") == "walkover")
            .then(pl.lit("WO"))
            .when(pl.col("result_type") == "injury")
            .then(pl.lit("INJ"))
            .otherwise(pl.lit(None, dtype=pl.String))
        )
    else:
        fallback_score_expr = pl.lit(None, dtype=pl.String)

    final_date_expr = (
        pl.col("start_date_utc").cast(pl.Date).alias("_final_start_date")
        if "start_date_utc" in finals_dedup.columns
        else pl.lit(None, dtype=pl.Date).alias("_final_start_date")
    )

    finals = finals_dedup.select(
        pl.col("event_sk").cast(pl.Int64),
        pl.col("gender_category").cast(pl.String),
        pl.col("match_sk").cast(pl.Int64).alias("final_match_sk"),
        pl.col("winner_player_sk").cast(pl.Int64),
        pl.col("loser_player_sk").cast(pl.Int64),
        pl.when(
            pl.col("games_won_p1").is_not_null()
            & pl.col("games_won_p2").is_not_null()
            & pl.col("p1_player_sk").is_not_null()
        )
        .then(
            pl.when(pl.col("winner_player_sk") == pl.col("p1_player_sk"))
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
        .otherwise(fallback_score_expr)
        .cast(pl.String)
        .alias("final_score"),
        final_date_expr,
    )

    ratings_lookup = ratings_with_prior.select(
        pl.col("match_sk").cast(pl.Int64),
        pl.col("rating_date").cast(pl.Date),
        pl.col("p1_player_sk").cast(pl.Int64).alias("_r_p1_sk"),
        pl.col("p1_pre_rating").cast(pl.Float64).alias("_r_p1_pre_rating"),
        pl.col("p2_pre_rating").cast(pl.Float64).alias("_r_p2_pre_rating"),
        pl.col("p1_expected_win_prob").cast(pl.Float64).alias("_r_p1_prob"),
        pl.col("p1_prior_matches").cast(pl.Int64).alias("_r_p1_prior"),
        pl.col("p2_prior_matches").cast(pl.Int64).alias("_r_p2_prior"),
    )

    champ_keys = finals.select(
        "event_sk",
        "gender_category",
        pl.col("winner_player_sk").alias("_champ_sk"),
    )

    champ_matches = (
        scoped.join(champ_keys, on=["event_sk", "gender_category"], how="inner")
        .filter(pl.col("winner_player_sk") == pl.col("_champ_sk"))
        .join(ratings_lookup, on="match_sk", how="left")
        .with_columns(
            pl.when(pl.col("_r_p1_sk").is_null())
            .then(pl.lit(None, dtype=pl.Float64))
            .when(pl.col("_champ_sk") == pl.col("_r_p1_sk"))
            .then(pl.col("_r_p1_prob"))
            .otherwise(1.0 - pl.col("_r_p1_prob"))
            .cast(pl.Float64)
            .alias("_champ_win_prob"),
            pl.when(pl.col("_r_p1_sk").is_null())
            .then(pl.lit(None, dtype=pl.Int64))
            .when(pl.col("_champ_sk") == pl.col("_r_p1_sk"))
            .then(pl.col("_r_p1_prior"))
            .otherwise(pl.col("_r_p2_prior"))
            .cast(pl.Int64)
            .alias("_champ_prior_matches"),
            pl.when(pl.col("_r_p1_sk").is_null())
            .then(pl.lit(None, dtype=pl.Float64))
            .when(pl.col("_champ_sk") == pl.col("_r_p1_sk"))
            .then(pl.col("_r_p1_pre_rating"))
            .otherwise(pl.col("_r_p2_pre_rating"))
            .cast(pl.Float64)
            .alias("_champ_pre_rating"),
        )
    )

    sort_path_cols = (
        ["round_order", "match_sk"]
        if "round_order" in champ_matches.columns
        else ["match_sk"]
    )
    champ_matches = champ_matches.sort(sort_path_cols, nulls_last=True)

    aggregated = (
        champ_matches.group_by(["event_sk", "gender_category"])
        .agg(
            pl.len().cast(pl.Int64).alias("wins_count"),
            pl.col("_champ_win_prob")
            .is_not_null()
            .sum()
            .cast(pl.Int64)
            .alias("rated_wins"),
            (
                100.0
                * pl.col("_champ_win_prob")
                .fill_null(1.0)
                .clip(1e-9, 1.0)
                .log()
                .sum()
                .exp()
            )
            .cast(pl.Float64)
            .alias("tournament_win_prob_pct"),
            pl.col("_champ_prior_matches")
            .drop_nulls()
            .min()
            .cast(pl.Int64)
            .alias("champ_start_prior_matches"),
            pl.col("_champ_pre_rating")
            .drop_nulls()
            .first()
            .cast(pl.Float64)
            .alias("champ_pre_rating"),
            pl.col("round_code")
            .drop_nulls()
            .first()
            .cast(pl.String)
            .alias("_first_round"),
            pl.col("rating_date")
            .drop_nulls()
            .max()
            .cast(pl.Date)
            .alias("_max_rating_date"),
        )
        .filter(
            (pl.col("rated_wins") >= 1)
            & (pl.col("champ_start_prior_matches").fill_null(0) >= min_prior_matches)
        )
    )

    if aggregated.is_empty():
        return _empty_unlikely_winners_table()

    org_expr = (
        pl.coalesce([pl.col("organization_code"), pl.col("country_code")])
        .fill_null("")
        .cast(pl.String)
        .str.strip_chars()
    )
    head_shot_expr = (
        pl.col("head_shot").cast(pl.String)
        if "head_shot" in players.columns
        else pl.lit(None, dtype=pl.String).alias("head_shot")
    )
    gender_expr = (
        pl.col("gender").cast(pl.String)
        if "gender" in players.columns
        else pl.lit(None, dtype=pl.String).alias("gender")
    )
    player_lookup = players.select(
        pl.col("player_sk").cast(pl.Int64),
        pl.col("player_name").fill_null("Unknown Player").cast(pl.String),
        org_expr.alias("_org"),
        head_shot_expr.alias("_head_shot"),
        gender_expr.alias("_player_gender"),
    )

    event_year_expr = (
        pl.col("event_year").cast(pl.Int64)
        if "event_year" in events.columns
        else pl.lit(None, dtype=pl.Int64).alias("event_year")
    )
    event_type_expr = (
        pl.col("event_type").fill_null("Other").cast(pl.String)
        if "event_type" in events.columns
        else pl.lit("Other", dtype=pl.String).alias("event_type")
    )
    event_start_expr = (
        pl.col("start_date").cast(pl.Date).alias("_event_start_date")
        if "start_date" in events.columns
        else pl.lit(None, dtype=pl.Date).alias("_event_start_date")
    )
    event_lookup = events.select(
        pl.col("event_sk").cast(pl.Int64),
        pl.col("event_name").fill_null("Unknown Event").cast(pl.String),
        event_year_expr,
        event_type_expr,
        event_start_expr,
    )

    enriched = (
        aggregated.join(finals, on=["event_sk", "gender_category"], how="inner")
        .join(event_lookup, on="event_sk", how="left")
        .join(
            player_lookup.select(
                pl.col("player_sk").alias("winner_player_sk"),
                pl.col("player_name").alias("winner_name"),
                pl.col("_org").alias("winner_org"),
                pl.col("_head_shot"),
                pl.col("_player_gender"),
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
        .with_columns(
            pl.col("event_name").fill_null("Unknown Event"),
            pl.col("event_type").fill_null("Other"),
            pl.col("winner_name").fill_null("Unknown Player"),
            pl.col("loser_name").fill_null("Unknown Player"),
            pl.col("winner_org").fill_null(""),
            pl.col("loser_org").fill_null(""),
            pl.coalesce(
                [
                    pl.col("_final_start_date"),
                    pl.col("_max_rating_date"),
                    pl.col("_event_start_date"),
                ]
            ).alias("final_date"),
            pl.when(
                pl.col("_first_round").is_not_null() & (pl.col("_first_round") != "F")
            )
            .then(pl.col("_first_round") + pl.lit(" → F"))
            .otherwise(pl.lit("F"))
            .alias("round_path"),
        )
        .with_columns(
            pl.coalesce(
                [
                    pl.col("event_year"),
                    pl.col("final_date").dt.year().cast(pl.Int64),
                ]
            ).alias("event_year"),
        )
    )

    tournament_type_vals = [
        map_tournament_type(t) for t in enriched["event_type"].to_list()
    ]
    enriched = enriched.with_columns(
        pl.Series("tournament_type", tournament_type_vals, dtype=pl.String)
    )

    if tournament_types:
        enriched = enriched.filter(
            pl.col("tournament_type").is_in(list(tournament_types))
        )

    if year is not None:
        enriched = enriched.filter(pl.col("event_year") == year)

    if enriched.is_empty():
        return _empty_unlikely_winners_table()

    sorted_df = enriched.sort(
        ["tournament_win_prob_pct", "final_date", "event_sk"],
        descending=[False, True, False],
        nulls_last=True,
    ).with_columns(pl.int_range(1, pl.len() + 1, dtype=pl.Int64).alias("rank"))

    placeholders = {
        "men": resolve_placeholder_data_uri(
            "men", theme=theme, placeholders_dir=placeholders_dir
        ),
        "women": resolve_placeholder_data_uri(
            "women", theme=theme, placeholders_dir=placeholders_dir
        ),
    }

    winner_flags = [flag_url_for_code(c) for c in sorted_df["winner_org"].to_list()]
    loser_flags = [flag_url_for_code(c) for c in sorted_df["loser_org"].to_list()]
    winner_photos = [
        sanitize_headshot_url(
            url,
            gender=p_gender or g_cat,
            theme=theme,
            placeholder_uri=placeholders.get(g_cat, placeholders["men"]),
            placeholders_dir=placeholders_dir,
        )
        for url, p_gender, g_cat in zip(
            sorted_df["_head_shot"].to_list(),
            sorted_df["_player_gender"].to_list(),
            sorted_df["gender_category"].to_list(),
            strict=True,
        )
    ]

    return sorted_df.with_columns(
        pl.Series("winner_flag_url", winner_flags, dtype=pl.String),
        pl.Series("loser_flag_url", loser_flags, dtype=pl.String),
        pl.Series("winner_photo_url", winner_photos, dtype=pl.String),
    ).select(
        "rank",
        "event_sk",
        "gender_category",
        "event_name",
        "event_year",
        "event_type",
        "tournament_type",
        "final_date",
        "final_match_sk",
        "winner_player_sk",
        "winner_name",
        "winner_org",
        "winner_flag_url",
        "winner_photo_url",
        "final_score",
        "loser_player_sk",
        "loser_name",
        "loser_org",
        "loser_flag_url",
        "champ_pre_rating",
        "champ_start_prior_matches",
        "wins_count",
        "rated_wins",
        "round_path",
        "tournament_win_prob_pct",
    )


def top_unlikely_winners_by_gender(
    df: pl.DataFrame,
    *,
    gender: Literal["men", "women"],
    limit: int = 10,
) -> pl.DataFrame:
    """Return the top ``limit`` most unlikely champions for one gender pool."""
    filtered = (
        df.filter(pl.col("gender_category") == gender)
        .sort(
            ["tournament_win_prob_pct", "final_date", "event_sk"],
            descending=[False, True, False],
            nulls_last=True,
        )
        .head(limit)
    )
    if filtered.is_empty():
        return filtered
    return filtered.with_columns(
        pl.int_range(1, pl.len() + 1, dtype=pl.Int64).alias("rank")
    )


def build_unlikely_winners_chart(
    df: pl.DataFrame,
    *,
    log_scale: bool = True,
) -> go.Figure:
    """Plot tournament date vs champion Glicko-2 tournament win probability."""
    fig = go.Figure()
    if df.is_empty():
        return style_figure(fig, height=400, legend_bottom=True)

    dated = df.filter(pl.col("final_date").is_not_null()).sort("final_date")
    if dated.is_empty():
        return style_figure(fig, height=400, legend_bottom=True)

    for gender_key, label in (("men", "Men"), ("women", "Women")):
        sub = dated.filter(pl.col("gender_category") == gender_key)
        if sub.is_empty():
            continue

        x_vals = sub["final_date"].to_list()
        y_vals = sub["tournament_win_prob_pct"].to_list()
        customdata = [
            (
                row["winner_name"],
                row["event_name"],
                str(row["event_year"] or ""),
                row["loser_name"],
                row["final_score"] or "—",
                row["round_path"],
                int(row["wins_count"]),
                format_win_prob_pct(float(row["tournament_win_prob_pct"])),
            )
            for row in sub.to_dicts()
        ]

        fig.add_trace(
            go.Scatter(
                x=x_vals,
                y=y_vals,
                mode="markers",
                name=f"{label}'s Singles",
                marker={
                    "color": GENDER_COLORS[label],
                    "size": 8.5,
                    "opacity": 0.82,
                    "line": {"width": 1.0, "color": "rgba(255,255,255,0.45)"},
                },
                customdata=customdata,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "%{customdata[1]} (%{customdata[2]})<br>"
                    "Knockout Run Chance: <b>%{customdata[7]}</b><br>"
                    "Final: def. %{customdata[3]} (%{customdata[4]})<br>"
                    "Path: %{customdata[5]} (%{customdata[6]} wins)<extra></extra>"
                ),
            )
        )

    fig.update_xaxes(
        title_text="Tournament Date",
        tickformat="%b %Y",
    )
    y_title = (
        "Glicko-2 Knockout Run Chance (%, log scale)"
        if log_scale
        else "Glicko-2 Knockout Run Chance (%)"
    )
    fig.update_yaxes(
        title_text=y_title,
        type="log" if log_scale else "linear",
        ticksuffix="%",
    )
    return style_figure(fig, height=400, legend_bottom=True)

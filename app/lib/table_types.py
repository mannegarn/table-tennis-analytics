"""Table Types: Glicko-2 Win-Rate Residuals, Upset Rates & China H0 Tests.

Pure Python + Polars + Plotly module (no Streamlit UI calls) so all surface
aggregations, paired contrasts, Fisher's exact tests, upset calibration
residuals, and chart builders can be unit-tested directly.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

import plotly.graph_objects as go  # type: ignore[import-untyped]
import polars as pl

from .charts import style_figure
from .glicko import continent_for_code, flag_url_for_code
from .upsets import attach_prior_match_counts

SURFACE_GRIPPY = "Grippy (Asian)"
SURFACE_SKIDDY = "Skiddy (European)"
SURFACE_OTHER = "Other / Unclassified"

SURFACE_ORDER: tuple[str, ...] = (
    SURFACE_GRIPPY,
    SURFACE_SKIDDY,
    SURFACE_OTHER,
)

# Restrained editorial palette (Tennis Abstract / FBref inspired, Light & Dark safe)
COLOR_GRIPPY = "#C26D45"
COLOR_SKIDDY = "#4A7C9B"
COLOR_OTHER = "#64748B"
COLOR_CHN = "#D97706"
COLOR_NON_CHN = "#3B82F6"

SURFACE_COLORS: dict[str, str] = {
    SURFACE_GRIPPY: COLOR_GRIPPY,
    SURFACE_SKIDDY: COLOR_SKIDDY,
    SURFACE_OTHER: COLOR_OTHER,
}

TIER_OPTIONS: tuple[str, ...] = (
    "Grand Smash",
    "Champions",
    "Star Contender",
    "Contender",
    "Feeder",
    "ITTF Majors / Other",
)

_EVENT_TYPE_TO_TIER: dict[str, str] = {
    "WTT Grand Smash": "Grand Smash",
    "WTT Champions": "Champions",
    "WTT Star Contender": "Star Contender",
    "WTT Contender": "Contender",
    "WTT Feeder": "Feeder",
}

_FALLBACK_BRAND_GROUP: dict[str, str] = {
    "Butterfly": "Asian",
    "DHS": "Asian",
    "Double Fish": "Asian",
    "Yinhe": "Asian",
    "Andro": "European",
    "Donic": "European",
    "Joola": "European",
    "Stag": "European",
    "Stiga": "European",
    "Tibhar": "European",
    "Nittaku": "Other",
    "San-Ei": "Other",
    "Xiom": "Other",
}

_Z_95 = 1.959964


@dataclass(frozen=True)
class SurfaceKPISummary:
    """Top KPI strip metrics for Table Type v2."""

    total_matches: int
    total_events: int
    top_sponsor: str | None
    top_sponsor_events: int
    top_sponsor_matches: int
    chn_h2h_asian_resid_pp: float | None
    chn_h2h_euro_resid_pp: float | None
    chn_h2h_gap_pp: float | None
    paired_chn_gap_pp: float | None
    paired_chn_p_value: float | None
    grippy_upset_resid_pp: float | None
    skiddy_upset_resid_pp: float | None
    upset_resid_gap_pp: float | None


@dataclass(frozen=True)
class PairedContrastSummary:
    """Paired within-player (Grippy - Skiddy) residual summary for CHN players."""

    n_players: int
    mean_diff_pp: float | None
    ci95_pp: float | None
    p_value: float | None
    n_better_on_asian: int
    n_better_on_european: int
    pct_better_on_asian: float | None
    players_df: pl.DataFrame


@dataclass(frozen=True)
class TiltAnalysisResult:
    """Results from grouping players by surface tilt (Grippy vs Skiddy)."""

    total_players: int
    neutral_excluded: int
    contingency_df: pl.DataFrame
    players_df: pl.DataFrame
    chn_asian_share_pct: float | None
    non_chn_asian_share_pct: float | None
    odds_ratio: float | None
    fisher_p_value: float | None
    chi2_p_value: float | None
    chn_mean_tilt_pp: float | None
    chn_tilt_ci95_pp: float | None
    non_chn_mean_tilt_pp: float | None
    non_chn_tilt_ci95_pp: float | None
    welch_diff_pp: float | None
    welch_p_value: float | None


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


def map_event_tier(event_type: str | None) -> str:
    """Map raw ``dim_events.event_type`` to a clean WTT tier bucket."""
    if not event_type:
        return "ITTF Majors / Other"
    return _EVENT_TYPE_TO_TIER.get(event_type.strip(), "ITTF Majors / Other")


def classify_surface_profile(
    table_sponsor: str | None,
    table_group: str | None = None,
) -> str:
    """Map table sponsor / group to Grippy (Asian), Skiddy (European), or Other."""
    group = (table_group or "").strip()
    if not group and table_sponsor:
        group = _FALLBACK_BRAND_GROUP.get(table_sponsor.strip(), "")
    if group == "Asian":
        return SURFACE_GRIPPY
    if group == "European":
        return SURFACE_SKIDDY
    return SURFACE_OTHER


def wilson_ci_95(wins: int, n: int) -> tuple[float, float]:
    """Return 95% Wilson score interval ``(low_pct, high_pct)`` in ``[0, 100]``."""
    if n <= 0:
        return 0.0, 0.0
    p = max(0.0, min(1.0, wins / n))
    z2 = _Z_95 * _Z_95
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = (_Z_95 * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)) / denom
    return max(0.0, 100.0 * (center - half)), min(100.0, 100.0 * (center + half))


def residual_ci95_pp(values: Sequence[float]) -> float | None:
    """Return 95% CI half-width (in percentage points) for 0..1 residual values."""
    if len(values) < 2:
        return None
    _, ci95, _ = paired_t_test([v * 100.0 for v in values])
    return ci95


def _hypergeometric_prob(a: int, r1: int, c1: int, n: int) -> float:
    log_p = (
        math.lgamma(r1 + 1)
        + math.lgamma(n - r1 + 1)
        + math.lgamma(c1 + 1)
        + math.lgamma(n - c1 + 1)
        - math.lgamma(n + 1)
        - math.lgamma(a + 1)
        - math.lgamma(r1 - a + 1)
        - math.lgamma(c1 - a + 1)
        - math.lgamma(n - r1 - c1 + a + 1)
    )
    return math.exp(log_p)


def fisher_exact_two_tailed(a: int, b: int, c: int, d: int) -> float | None:
    """Exact two-tailed Fisher test p-value for a 2x2 table ``[[a, b], [c, d]]``."""
    if min(a, b, c, d) < 0:
        return None
    r1 = a + b
    r2 = c + d
    c1 = a + c
    c2 = b + d
    n = r1 + r2
    if n == 0 or r1 == 0 or r2 == 0 or c1 == 0 or c2 == 0:
        return 1.0 if n > 0 else None

    p_obs = _hypergeometric_prob(a, r1, c1, n)
    lo = max(0, r1 + c1 - n)
    hi = min(r1, c1)
    total_p = 0.0
    tol = 1e-12
    for x in range(lo, hi + 1):
        p_x = _hypergeometric_prob(x, r1, c1, n)
        if p_x <= p_obs * (1.0 + tol) + 1e-15:
            total_p += p_x
    return min(1.0, max(0.0, total_p))


def chi_square_2x2_p_value(a: int, b: int, c: int, d: int) -> float | None:
    """Pearson chi-square two-tailed p-value (df=1) for ``[[a, b], [c, d]]``."""
    if min(a, b, c, d) < 0:
        return None
    r1 = a + b
    r2 = c + d
    c1 = a + c
    c2 = b + d
    n = r1 + r2
    denom = r1 * r2 * c1 * c2
    if n == 0 or denom == 0:
        return None
    num = (a * d - b * c) ** 2 * n
    chi2 = num / denom
    return max(0.0, min(1.0, math.erfc(math.sqrt(chi2 / 2.0))))


def _empty_surface_matches() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "match_sk": pl.Int64,
            "event_sk": pl.Int64,
            "gender_category": pl.String,
            "rating_date": pl.Date,
            "p1_player_sk": pl.Int64,
            "p2_player_sk": pl.Int64,
            "p1_pre_rating": pl.Float64,
            "p2_pre_rating": pl.Float64,
            "p1_expected_win_prob": pl.Float64,
            "p1_actual_score": pl.Float64,
            "p1_prior_matches": pl.Int64,
            "p2_prior_matches": pl.Int64,
            "event_name": pl.String,
            "event_type": pl.String,
            "tier": pl.String,
            "table_sponsor": pl.String,
            "table_group": pl.String,
            "surface_profile": pl.String,
            "p1_org": pl.String,
            "p2_org": pl.String,
            "p1_is_chn": pl.Boolean,
            "p2_is_chn": pl.Boolean,
            "is_chn_vs_non_chn": pl.Boolean,
            "rating_gap": pl.Float64,
            "underdog_prob": pl.Float64,
            "is_underdog_40": pl.Boolean,
            "is_upset": pl.Boolean,
        }
    )


def prepare_surface_matches(
    ratings: pl.DataFrame,
    events: pl.DataFrame,
    players: pl.DataFrame,
    table_brands: pl.DataFrame,
    *,
    gender: Literal["all", "men", "women"] = "all",
    tiers: Sequence[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    min_prior_matches: int = 5,
) -> pl.DataFrame:
    """Build match-level surface DataFrame with Glicko-2 probs, tiers, and upsets."""
    if ratings.is_empty() or events.is_empty():
        return _empty_surface_matches()

    with_prior = (
        ratings
        if (
            "p1_prior_matches" in ratings.columns
            and "p2_prior_matches" in ratings.columns
        )
        else attach_prior_match_counts(ratings)
    )

    event_cols = events.select(
        pl.col("event_sk").cast(pl.Int64),
        pl.col("event_name").fill_null("Unknown Event").cast(pl.String),
        pl.col("event_type").cast(pl.String),
        pl.col("table_sponsor").cast(pl.String).str.strip_chars(),
    ).filter(pl.col("table_sponsor").is_not_null() & (pl.col("table_sponsor") != ""))

    brand_cols = (
        table_brands.select(
            pl.col("table_sponsor").cast(pl.String).str.strip_chars(),
            pl.col("table_group").cast(pl.String).str.strip_chars(),
        ).unique(subset=["table_sponsor"])
        if not table_brands.is_empty()
        else pl.DataFrame(schema={"table_sponsor": pl.String, "table_group": pl.String})
    )

    player_orgs = players.select(
        pl.col("player_sk").cast(pl.Int64),
        pl.coalesce([pl.col("organization_code"), pl.col("country_code")])
        .fill_null("")
        .cast(pl.String)
        .str.strip_chars()
        .str.to_uppercase()
        .alias("_org"),
    )

    joined = (
        with_prior.filter(pl.col("p1_actual_score") != 0.5)
        .select(
            pl.col("match_sk").cast(pl.Int64),
            pl.col("event_sk").cast(pl.Int64),
            pl.col("gender_category").cast(pl.String),
            pl.col("rating_date").cast(pl.Date),
            pl.col("p1_player_sk").cast(pl.Int64),
            pl.col("p2_player_sk").cast(pl.Int64),
            pl.col("p1_pre_rating").cast(pl.Float64),
            pl.col("p2_pre_rating").cast(pl.Float64),
            pl.col("p1_expected_win_prob").cast(pl.Float64),
            pl.col("p1_actual_score").cast(pl.Float64),
            pl.col("p1_prior_matches").cast(pl.Int64),
            pl.col("p2_prior_matches").cast(pl.Int64),
        )
        .join(event_cols, on="event_sk", how="inner")
        .join(brand_cols, on="table_sponsor", how="left")
        .join(
            player_orgs.rename({"player_sk": "p1_player_sk", "_org": "p1_org"}),
            on="p1_player_sk",
            how="left",
        )
        .join(
            player_orgs.rename({"player_sk": "p2_player_sk", "_org": "p2_org"}),
            on="p2_player_sk",
            how="left",
        )
        .with_columns(
            pl.col("p1_org").fill_null(""),
            pl.col("p2_org").fill_null(""),
            pl.col("event_type")
            .map_elements(map_event_tier, return_dtype=pl.String)
            .alias("tier"),
            pl.struct(["table_sponsor", "table_group"])
            .map_elements(
                lambda r: classify_surface_profile(
                    r["table_sponsor"], r["table_group"]
                ),
                return_dtype=pl.String,
            )
            .alias("surface_profile"),
        )
        .with_columns(
            (pl.col("p1_org") == "CHN").alias("p1_is_chn"),
            (pl.col("p2_org") == "CHN").alias("p2_is_chn"),
            (pl.col("p1_pre_rating") - pl.col("p2_pre_rating"))
            .abs()
            .alias("rating_gap"),
            pl.min_horizontal(
                pl.col("p1_expected_win_prob"),
                1.0 - pl.col("p1_expected_win_prob"),
            ).alias("underdog_prob"),
            (
                (
                    (pl.col("p1_expected_win_prob") < 0.5)
                    & (pl.col("p1_actual_score") == 1.0)
                )
                | (
                    (pl.col("p1_expected_win_prob") > 0.5)
                    & (pl.col("p1_actual_score") == 0.0)
                )
            ).alias("is_upset"),
        )
        .with_columns(
            (pl.col("p1_is_chn") != pl.col("p2_is_chn")).alias("is_chn_vs_non_chn"),
            (pl.col("underdog_prob") < 0.4).alias("is_underdog_40"),
        )
    )

    if gender != "all":
        joined = joined.filter(pl.col("gender_category") == gender)
    if tiers:
        joined = joined.filter(pl.col("tier").is_in(list(tiers)))
    if start_date is not None:
        joined = joined.filter(
            pl.col("rating_date").is_not_null() & (pl.col("rating_date") >= start_date)
        )
    if end_date is not None:
        joined = joined.filter(
            pl.col("rating_date").is_not_null() & (pl.col("rating_date") <= end_date)
        )
    if min_prior_matches > 0:
        joined = joined.filter(
            (pl.col("p1_prior_matches") >= min_prior_matches)
            & (pl.col("p2_prior_matches") >= min_prior_matches)
        )

    if joined.is_empty():
        return _empty_surface_matches()
    return joined.select(_empty_surface_matches().columns)


def _empty_player_matches() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "match_sk": pl.Int64,
            "event_sk": pl.Int64,
            "gender_category": pl.String,
            "rating_date": pl.Date,
            "player_sk": pl.Int64,
            "opp_sk": pl.Int64,
            "table_sponsor": pl.String,
            "surface_profile": pl.String,
            "tier": pl.String,
            "actual_score": pl.Float64,
            "expected_win_prob": pl.Float64,
            "residual": pl.Float64,
            "is_chn": pl.Boolean,
            "opp_is_chn": pl.Boolean,
            "is_chn_vs_non_chn": pl.Boolean,
            "player_name": pl.String,
            "org": pl.String,
            "country_name": pl.String,
            "continent": pl.String,
            "flag_url": pl.String,
            "style": pl.String,
            "grip": pl.String,
            "hand": pl.String,
        }
    )


def unpivot_surface_player_matches(
    surface_matches: pl.DataFrame,
    players: pl.DataFrame,
) -> pl.DataFrame:
    """Convert match-level surface rows to 2-rows-per-match player-residual frame."""
    if surface_matches.is_empty():
        return _empty_player_matches()

    p1 = surface_matches.select(
        "match_sk",
        "event_sk",
        "gender_category",
        "rating_date",
        pl.col("p1_player_sk").alias("player_sk"),
        pl.col("p2_player_sk").alias("opp_sk"),
        "table_sponsor",
        "surface_profile",
        "tier",
        pl.col("p1_actual_score").alias("actual_score"),
        pl.col("p1_expected_win_prob").alias("expected_win_prob"),
        pl.col("p1_is_chn").alias("is_chn"),
        pl.col("p2_is_chn").alias("opp_is_chn"),
        "is_chn_vs_non_chn",
    )
    p2 = surface_matches.select(
        "match_sk",
        "event_sk",
        "gender_category",
        "rating_date",
        pl.col("p2_player_sk").alias("player_sk"),
        pl.col("p1_player_sk").alias("opp_sk"),
        "table_sponsor",
        "surface_profile",
        "tier",
        (1.0 - pl.col("p1_actual_score")).alias("actual_score"),
        (1.0 - pl.col("p1_expected_win_prob")).alias("expected_win_prob"),
        pl.col("p2_is_chn").alias("is_chn"),
        pl.col("p1_is_chn").alias("opp_is_chn"),
        "is_chn_vs_non_chn",
    )

    player_meta = players.select(
        pl.col("player_sk").cast(pl.Int64),
        pl.col("player_name").fill_null("Unknown Player").cast(pl.String),
        pl.coalesce([pl.col("organization_code"), pl.col("country_code")])
        .fill_null("")
        .cast(pl.String)
        .str.strip_chars()
        .str.to_uppercase()
        .alias("org"),
        pl.coalesce([pl.col("country_name"), pl.col("organization_name")])
        .fill_null("")
        .cast(pl.String)
        .alias("country_name"),
        pl.col("style").fill_null("Unknown").cast(pl.String),
        pl.col("grip").fill_null("Unknown").cast(pl.String),
        pl.col("hand").fill_null("Unknown").cast(pl.String),
    )
    unique_orgs = player_meta["org"].unique().to_list()
    flag_map = {code: flag_url_for_code(code) for code in unique_orgs}
    cont_map = {code: continent_for_code(code) for code in unique_orgs}
    player_meta = player_meta.with_columns(
        pl.col("org")
        .replace_strict(flag_map, default=None, return_dtype=pl.String)
        .alias("flag_url"),
        pl.col("org")
        .replace_strict(cont_map, default="Unknown", return_dtype=pl.String)
        .alias("continent"),
    )

    return (
        pl.concat([p1, p2], how="vertical")
        .with_columns(
            (pl.col("actual_score") - pl.col("expected_win_prob")).alias("residual")
        )
        .join(player_meta, on="player_sk", how="left")
        .with_columns(
            pl.col("player_name").fill_null("Unknown Player"),
            pl.col("org").fill_null(""),
            pl.col("country_name").fill_null(""),
            pl.col("continent").fill_null("Unknown"),
            pl.col("style").fill_null("Unknown"),
            pl.col("grip").fill_null("Unknown"),
            pl.col("hand").fill_null("Unknown"),
        )
        .select(_empty_player_matches().columns)
    )


def _mean_resid_pp(df: pl.DataFrame) -> float | None:
    if df.is_empty():
        return None
    val = df["residual"].mean()
    return float(val) * 100.0 if isinstance(val, (int, float)) else None


def _upset_resid_pp(df: pl.DataFrame) -> float | None:
    dogs = df.filter(pl.col("is_underdog_40"))
    if dogs.is_empty():
        return None
    act = dogs["is_upset"].cast(pl.Float64).mean()
    exp = dogs["underdog_prob"].mean()
    if isinstance(act, (int, float)) and isinstance(exp, (int, float)):
        return (float(act) - float(exp)) * 100.0
    return None


def compute_kpi_summary(
    surface_matches: pl.DataFrame,
    player_matches: pl.DataFrame,
    *,
    min_matches_paired: int = 5,
) -> SurfaceKPISummary:
    """Compute top-level KPI metrics across tracked surface matches."""
    if surface_matches.is_empty():
        return SurfaceKPISummary(
            total_matches=0,
            total_events=0,
            top_sponsor=None,
            top_sponsor_events=0,
            top_sponsor_matches=0,
            chn_h2h_asian_resid_pp=None,
            chn_h2h_euro_resid_pp=None,
            chn_h2h_gap_pp=None,
            paired_chn_gap_pp=None,
            paired_chn_p_value=None,
            grippy_upset_resid_pp=None,
            skiddy_upset_resid_pp=None,
            upset_resid_gap_pp=None,
        )

    sponsor_counts = (
        surface_matches.group_by("table_sponsor")
        .agg(
            pl.col("event_sk").n_unique().cast(pl.Int64).alias("events"),
            pl.len().cast(pl.Int64).alias("matches"),
        )
        .sort(["matches", "events", "table_sponsor"], descending=[True, True, False])
    )
    top_row = sponsor_counts.row(0, named=True)

    chn_h2h = player_matches.filter(pl.col("is_chn") & pl.col("is_chn_vs_non_chn"))
    chn_asian = _mean_resid_pp(
        chn_h2h.filter(pl.col("surface_profile") == SURFACE_GRIPPY)
    )
    chn_euro = _mean_resid_pp(
        chn_h2h.filter(pl.col("surface_profile") == SURFACE_SKIDDY)
    )
    chn_gap = (
        (chn_asian - chn_euro)
        if (chn_asian is not None and chn_euro is not None)
        else None
    )

    paired = compute_paired_chn_contrast(
        player_matches,
        min_matches_per_surface=min_matches_paired,
        chn_h2h_only=True,
    )

    grippy_up = _upset_resid_pp(
        surface_matches.filter(pl.col("surface_profile") == SURFACE_GRIPPY)
    )
    skiddy_up = _upset_resid_pp(
        surface_matches.filter(pl.col("surface_profile") == SURFACE_SKIDDY)
    )
    up_gap = (
        (skiddy_up - grippy_up)
        if (skiddy_up is not None and grippy_up is not None)
        else None
    )

    return SurfaceKPISummary(
        total_matches=surface_matches.height,
        total_events=int(surface_matches["event_sk"].n_unique()),
        top_sponsor=str(top_row["table_sponsor"]),
        top_sponsor_events=int(top_row["events"]),
        top_sponsor_matches=int(top_row["matches"]),
        chn_h2h_asian_resid_pp=chn_asian,
        chn_h2h_euro_resid_pp=chn_euro,
        chn_h2h_gap_pp=chn_gap,
        paired_chn_gap_pp=paired.mean_diff_pp,
        paired_chn_p_value=paired.p_value,
        grippy_upset_resid_pp=grippy_up,
        skiddy_upset_resid_pp=skiddy_up,
        upset_resid_gap_pp=up_gap,
    )


def build_cohort_residuals(
    player_matches: pl.DataFrame,
    *,
    grouping: Literal["surface", "brand"] = "surface",
    chn_h2h_only: bool = True,
    min_matches: int = 50,
) -> pl.DataFrame:
    """Compute Win % Over Expected (with 95% CI) for China vs Non-China cohorts."""
    schema = {
        "group_label": pl.String,
        "surface_profile": pl.String,
        "cohort": pl.String,
        "n": pl.Int64,
        "actual_win_pct": pl.Float64,
        "expected_win_pct": pl.Float64,
        "residual_pp": pl.Float64,
        "ci95_pp": pl.Float64,
        "total_group_n": pl.Int64,
    }
    if player_matches.is_empty():
        return pl.DataFrame(schema=schema)

    group_col = "surface_profile" if grouping == "surface" else "table_sponsor"

    if grouping == "brand":
        eligible_brands = (
            player_matches.group_by("table_sponsor")
            .agg(pl.col("match_sk").n_unique().alias("distinct_matches"))
            .filter(pl.col("distinct_matches") >= min_matches)["table_sponsor"]
            .to_list()
        )
        scoped = player_matches.filter(pl.col("table_sponsor").is_in(eligible_brands))
    else:
        scoped = player_matches

    if scoped.is_empty():
        return pl.DataFrame(schema=schema)

    group_totals = (
        scoped.group_by(group_col)
        .agg(pl.col("match_sk").n_unique().cast(pl.Int64).alias("total_group_n"))
        .to_dicts()
    )
    total_map = {str(r[group_col]): int(r["total_group_n"]) for r in group_totals}

    profile_map = (
        {grp: grp for grp in total_map}
        if grouping == "surface"
        else {
            str(r["table_sponsor"]): str(r["surface_profile"])
            for r in scoped.select("table_sponsor", "surface_profile")
            .unique()
            .to_dicts()
        }
    )

    chn_filter = (
        (pl.col("is_chn") & pl.col("is_chn_vs_non_chn"))
        if chn_h2h_only
        else pl.col("is_chn")
    )
    chn_label = "China (CHN vs Non-CHN)" if chn_h2h_only else "China (CHN, All)"
    cohort_frames = [
        (chn_label, scoped.filter(chn_filter)),
        ("Non-China", scoped.filter(~pl.col("is_chn"))),
    ]

    if grouping == "surface":
        ordered_groups = [g for g in SURFACE_ORDER if g in total_map]
    else:
        ordered_groups = sorted(
            total_map.keys(),
            key=lambda g: (-total_map[g], g),
        )

    rows: list[dict[str, object]] = []
    for grp in ordered_groups:
        for cohort_name, cdf in cohort_frames:
            sub = cdf.filter(pl.col(group_col) == grp)
            n = sub.height
            if n == 0:
                continue
            resids = sub["residual"].to_list()
            act_mean = float(sub["actual_score"].mean()) * 100.0  # type: ignore[arg-type]
            exp_mean = float(sub["expected_win_prob"].mean()) * 100.0  # type: ignore[arg-type]
            res_mean = float(sub["residual"].mean()) * 100.0  # type: ignore[arg-type]
            ci95 = residual_ci95_pp(resids)
            rows.append(
                {
                    "group_label": grp,
                    "surface_profile": profile_map.get(grp, SURFACE_OTHER),
                    "cohort": cohort_name,
                    "n": n,
                    "actual_win_pct": act_mean,
                    "expected_win_pct": exp_mean,
                    "residual_pp": res_mean,
                    "ci95_pp": ci95,
                    "total_group_n": total_map[grp],
                }
            )

    if not rows:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame(rows, schema=schema)


def _compute_per_player_surface_contrast(
    player_matches: pl.DataFrame,
    *,
    min_matches_per_surface: int,
    chn_h2h_only: bool,
) -> pl.DataFrame:
    """Return 1 row per player with >= min_matches on BOTH Grippy and Skiddy."""
    scoped = player_matches.filter(
        pl.col("surface_profile").is_in([SURFACE_GRIPPY, SURFACE_SKIDDY])
    )
    if chn_h2h_only:
        scoped = scoped.filter((~pl.col("is_chn")) | pl.col("is_chn_vs_non_chn"))

    if scoped.is_empty():
        return pl.DataFrame()

    per_surface = (
        scoped.group_by(
            ["player_sk", "player_name", "org", "flag_url", "is_chn", "surface_profile"]
        )
        .agg(
            pl.len().cast(pl.Int64).alias("n"),
            (pl.col("residual").mean() * 100.0).alias("resid_pp"),
        )
        .filter(pl.col("n") >= min_matches_per_surface)
    )
    if per_surface.is_empty():
        return pl.DataFrame()

    piv_resid = per_surface.pivot(
        on="surface_profile",
        index=["player_sk", "player_name", "org", "flag_url", "is_chn"],
        values="resid_pp",
    )
    piv_n = per_surface.pivot(
        on="surface_profile",
        index=["player_sk"],
        values="n",
    )
    if (
        SURFACE_GRIPPY not in piv_resid.columns
        or SURFACE_SKIDDY not in piv_resid.columns
    ):
        return pl.DataFrame()

    joined = piv_resid.join(
        piv_n.select(
            "player_sk",
            pl.col(SURFACE_GRIPPY).alias("grippy_matches"),
            pl.col(SURFACE_SKIDDY).alias("skiddy_matches"),
        ),
        on="player_sk",
        how="inner",
    ).drop_nulls(subset=[SURFACE_GRIPPY, SURFACE_SKIDDY])

    if joined.is_empty():
        return pl.DataFrame()

    return joined.rename(
        {
            SURFACE_GRIPPY: "grippy_resid_pp",
            SURFACE_SKIDDY: "skiddy_resid_pp",
        }
    ).with_columns(
        (pl.col("grippy_resid_pp") - pl.col("skiddy_resid_pp")).alias("tilt_pp")
    )


def compute_paired_chn_contrast(
    player_matches: pl.DataFrame,
    *,
    min_matches_per_surface: int = 5,
    chn_h2h_only: bool = True,
) -> PairedContrastSummary:
    """Test whether individual CHN players shift between Grippy and Skiddy tables."""
    empty_df = pl.DataFrame(
        schema={
            "player_sk": pl.Int64,
            "flag_url": pl.String,
            "player_name": pl.String,
            "org": pl.String,
            "grippy_matches": pl.Int64,
            "grippy_resid_pp": pl.Float64,
            "skiddy_matches": pl.Int64,
            "skiddy_resid_pp": pl.Float64,
            "tilt_pp": pl.Float64,
        }
    )
    chn_only = player_matches.filter(pl.col("is_chn"))
    contrasts = _compute_per_player_surface_contrast(
        chn_only,
        min_matches_per_surface=min_matches_per_surface,
        chn_h2h_only=chn_h2h_only,
    )
    if contrasts.is_empty():
        return PairedContrastSummary(
            n_players=0,
            mean_diff_pp=None,
            ci95_pp=None,
            p_value=None,
            n_better_on_asian=0,
            n_better_on_european=0,
            pct_better_on_asian=None,
            players_df=empty_df,
        )

    diffs = [float(v) for v in contrasts["tilt_pp"].to_list()]
    mean_diff, ci95, p_val = paired_t_test(diffs)
    n_asian = sum(1 for v in diffs if v > 0.0)
    n_euro = sum(1 for v in diffs if v < 0.0)
    pct_asian = 100.0 * n_asian / len(diffs) if diffs else None

    return PairedContrastSummary(
        n_players=len(diffs),
        mean_diff_pp=mean_diff,
        ci95_pp=ci95,
        p_value=p_val,
        n_better_on_asian=n_asian,
        n_better_on_european=n_euro,
        pct_better_on_asian=pct_asian,
        players_df=contrasts.sort("tilt_pp", descending=True).select(empty_df.columns),
    )


def build_player_tilt_analysis(
    player_matches: pl.DataFrame,
    *,
    min_matches_per_surface: int = 10,
    dead_zone_pp: float = 0.0,
    chn_h2h_only: bool = False,
) -> TiltAnalysisResult:
    """Group players by stronger surface and test CHN overrepresentation."""
    cont_schema = {
        "tilt_group": pl.String,
        "total_players": pl.Int64,
        "chn_players": pl.Int64,
        "non_chn_players": pl.Int64,
        "pct_chn": pl.Float64,
        "pct_chn_ci_low": pl.Float64,
        "pct_chn_ci_high": pl.Float64,
    }
    players_schema = {
        "player_sk": pl.Int64,
        "flag_url": pl.String,
        "player_name": pl.String,
        "org": pl.String,
        "is_chn": pl.Boolean,
        "tilt_group": pl.String,
        "tilt_pp": pl.Float64,
        "grippy_matches": pl.Int64,
        "grippy_resid_pp": pl.Float64,
        "skiddy_matches": pl.Int64,
        "skiddy_resid_pp": pl.Float64,
    }

    contrasts = _compute_per_player_surface_contrast(
        player_matches,
        min_matches_per_surface=min_matches_per_surface,
        chn_h2h_only=chn_h2h_only,
    )
    if contrasts.is_empty():
        return TiltAnalysisResult(
            total_players=0,
            neutral_excluded=0,
            contingency_df=pl.DataFrame(schema=cont_schema),
            players_df=pl.DataFrame(schema=players_schema),
            chn_asian_share_pct=None,
            non_chn_asian_share_pct=None,
            odds_ratio=None,
            fisher_p_value=None,
            chi2_p_value=None,
            chn_mean_tilt_pp=None,
            chn_tilt_ci95_pp=None,
            non_chn_mean_tilt_pp=None,
            non_chn_tilt_ci95_pp=None,
            welch_diff_pp=None,
            welch_p_value=None,
        )

    labeled = contrasts.with_columns(
        pl.when(pl.col("tilt_pp") > dead_zone_pp)
        .then(pl.lit("Better on Grippy (Asian)"))
        .when(pl.col("tilt_pp") < -dead_zone_pp)
        .then(pl.lit("Better on Skiddy (European)"))
        .otherwise(pl.lit("Neutral / Tie"))
        .alias("tilt_group")
    )

    neutral_count = labeled.filter(pl.col("tilt_group") == "Neutral / Tie").height
    decided = labeled.filter(pl.col("tilt_group") != "Neutral / Tie")

    asian_grp = decided.filter(pl.col("tilt_group") == "Better on Grippy (Asian)")
    euro_grp = decided.filter(pl.col("tilt_group") == "Better on Skiddy (European)")

    a = asian_grp.filter(pl.col("is_chn")).height  # Better on Asian & CHN
    b = asian_grp.filter(~pl.col("is_chn")).height  # Better on Asian & Non-CHN
    c = euro_grp.filter(pl.col("is_chn")).height  # Better on Euro & CHN
    d = euro_grp.filter(~pl.col("is_chn")).height  # Better on Euro & Non-CHN

    cont_rows: list[dict[str, object]] = []
    for grp_name, chn_cnt, non_cnt in (
        ("Better on Grippy (Asian)", a, b),
        ("Better on Skiddy (European)", c, d),
    ):
        tot = chn_cnt + non_cnt
        pct = 100.0 * chn_cnt / tot if tot > 0 else 0.0
        ci_lo, ci_hi = wilson_ci_95(chn_cnt, tot)
        cont_rows.append(
            {
                "tilt_group": grp_name,
                "total_players": tot,
                "chn_players": chn_cnt,
                "non_chn_players": non_cnt,
                "pct_chn": pct,
                "pct_chn_ci_low": ci_lo,
                "pct_chn_ci_high": ci_hi,
            }
        )

    chn_tot = a + c
    non_tot = b + d
    chn_asian_share = 100.0 * a / chn_tot if chn_tot > 0 else None
    non_chn_asian_share = 100.0 * b / non_tot if non_tot > 0 else None
    odds_ratio = (a * d) / (b * c) if (b * c) > 0 else None
    fisher_p = fisher_exact_two_tailed(a, b, c, d)
    chi2_p = chi_square_2x2_p_value(a, b, c, d)

    chn_tilts = [
        float(v) for v in contrasts.filter(pl.col("is_chn"))["tilt_pp"].to_list()
    ]
    non_tilts = [
        float(v) for v in contrasts.filter(~pl.col("is_chn"))["tilt_pp"].to_list()
    ]
    chn_mean, chn_ci, _ = paired_t_test(chn_tilts)
    non_mean, non_ci, _ = paired_t_test(non_tilts)
    welch_diff, _, welch_p = welch_t_test(chn_tilts, non_tilts)

    return TiltAnalysisResult(
        total_players=decided.height,
        neutral_excluded=neutral_count,
        contingency_df=pl.DataFrame(cont_rows, schema=cont_schema),
        players_df=labeled.sort("tilt_pp", descending=True).select(
            list(players_schema.keys())
        ),
        chn_asian_share_pct=chn_asian_share,
        non_chn_asian_share_pct=non_chn_asian_share,
        odds_ratio=odds_ratio,
        fisher_p_value=fisher_p,
        chi2_p_value=chi2_p,
        chn_mean_tilt_pp=chn_mean,
        chn_tilt_ci95_pp=chn_ci,
        non_chn_mean_tilt_pp=non_mean,
        non_chn_tilt_ci95_pp=non_ci,
        welch_diff_pp=welch_diff,
        welch_p_value=welch_p,
    )


def _summarize_upset_and_residuals(
    m_df: pl.DataFrame,
    pm_df: pl.DataFrame,
    *,
    group_col: str,
    group_val: str,
    surface_profile: str,
) -> dict[str, object]:
    m_sub = m_df.filter(pl.col(group_col) == group_val)
    dogs = m_sub.filter(pl.col("is_underdog_40"))
    n_dogs = dogs.height
    if n_dogs > 0:
        act_up = float(dogs["is_upset"].cast(pl.Float64).mean()) * 100.0  # type: ignore[arg-type]
        exp_up = float(dogs["underdog_prob"].mean()) * 100.0  # type: ignore[arg-type]
        up_res = act_up - exp_up
        up_ci = residual_ci95_pp(
            [
                float(u) - float(p)
                for u, p in zip(
                    dogs["is_upset"].cast(pl.Float64).to_list(),
                    dogs["underdog_prob"].to_list(),
                    strict=True,
                )
            ]
        )
    else:
        act_up = None
        exp_up = None
        up_res = None
        up_ci = None

    pm_sub = pm_df.filter(pl.col(group_col) == group_val)
    chn_h2h = pm_sub.filter(pl.col("is_chn") & pl.col("is_chn_vs_non_chn"))
    non_chn = pm_sub.filter(~pl.col("is_chn"))

    return {
        "group_name": group_val,
        "surface_profile": surface_profile,
        "events": int(m_sub["event_sk"].n_unique()),
        "matches": m_sub.height,
        "avg_rating_gap": float(m_sub["rating_gap"].mean()),  # type: ignore[arg-type]
        "underdog_40_matches": n_dogs,
        "upset_rate_pct": act_up,
        "expected_upset_pct": exp_up,
        "upset_residual_pp": up_res,
        "upset_ci95_pp": up_ci,
        "chn_h2h_matches": chn_h2h.height,
        "chn_h2h_residual_pp": _mean_resid_pp(chn_h2h),
        "non_chn_matches": non_chn.height,
        "non_chn_residual_pp": _mean_resid_pp(non_chn),
    }


def build_brand_summary_matrix(
    surface_matches: pl.DataFrame,
    player_matches: pl.DataFrame,
    *,
    min_matches: int = 50,
    grouping: Literal["brand", "surface"] = "brand",
) -> pl.DataFrame:
    """Build Table Brand (or Surface Profile) summary & upset rate matrix."""
    schema = {
        "group_name": pl.String,
        "surface_profile": pl.String,
        "events": pl.Int64,
        "matches": pl.Int64,
        "avg_rating_gap": pl.Float64,
        "underdog_40_matches": pl.Int64,
        "upset_rate_pct": pl.Float64,
        "expected_upset_pct": pl.Float64,
        "upset_residual_pp": pl.Float64,
        "upset_ci95_pp": pl.Float64,
        "chn_h2h_matches": pl.Int64,
        "chn_h2h_residual_pp": pl.Float64,
        "non_chn_matches": pl.Int64,
        "non_chn_residual_pp": pl.Float64,
    }
    if surface_matches.is_empty():
        return pl.DataFrame(schema=schema)

    if grouping == "surface":
        present = set(surface_matches["surface_profile"].unique().to_list())
        ordered = [s for s in SURFACE_ORDER if s in present]
        rows = [
            _summarize_upset_and_residuals(
                surface_matches,
                player_matches,
                group_col="surface_profile",
                group_val=s,
                surface_profile=s,
            )
            for s in ordered
        ]
        return pl.DataFrame(rows, schema=schema)

    brand_counts = (
        surface_matches.group_by(["table_sponsor", "surface_profile"])
        .agg(pl.len().alias("matches"))
        .filter(pl.col("matches") >= min_matches)
        .sort(["matches", "table_sponsor"], descending=[True, False])
    )
    if brand_counts.is_empty():
        return pl.DataFrame(schema=schema)

    rows = [
        _summarize_upset_and_residuals(
            surface_matches,
            player_matches,
            group_col="table_sponsor",
            group_val=str(r["table_sponsor"]),
            surface_profile=str(r["surface_profile"]),
        )
        for r in brand_counts.to_dicts()
    ]
    return pl.DataFrame(rows, schema=schema)


def build_player_surface_boosts(
    player_matches: pl.DataFrame,
    *,
    target_scope: str = "Asian vs European Boost",
    min_matches: int = 15,
    origin_filter: Literal["All", "China (CHN)", "Non-China"] = "All",
    search_query: str = "",
    chn_h2h_only: bool = False,
    sort_mode: Literal["Overperformers", "Underperformers", "Largest Magnitude"] = (
        "Overperformers"
    ),
) -> pl.DataFrame:
    """Rank individual players by Win % Over Expected on a surface/brand or boost."""
    schema = {
        "rank": pl.Int64,
        "player_sk": pl.Int64,
        "flag_url": pl.String,
        "player_name": pl.String,
        "org": pl.String,
        "is_chn": pl.Boolean,
        "matches": pl.Int64,
        "actual_wl": pl.String,
        "wins": pl.Int64,
        "losses": pl.Int64,
        "expected_wins": pl.Float64,
        "win_pct_over_expected": pl.Float64,
        "ci95_pp": pl.Float64,
        "abs_edge": pl.Float64,
    }
    if player_matches.is_empty():
        return pl.DataFrame(schema=schema)

    scoped = player_matches
    if chn_h2h_only:
        scoped = scoped.filter((~pl.col("is_chn")) | pl.col("is_chn_vs_non_chn"))
    if origin_filter == "China (CHN)":
        scoped = scoped.filter(pl.col("is_chn"))
    elif origin_filter == "Non-China":
        scoped = scoped.filter(~pl.col("is_chn"))

    if search_query.strip():
        needle = search_query.strip().lower()
        scoped = scoped.filter(
            pl.col("player_name").str.to_lowercase().str.contains(needle, literal=True)
        )

    if scoped.is_empty():
        return pl.DataFrame(schema=schema)

    if target_scope == "Asian vs European Boost":
        contrasts = _compute_per_player_surface_contrast(
            scoped,
            min_matches_per_surface=min_matches,
            chn_h2h_only=False,  # already filtered above if enabled
        )
        if contrasts.is_empty():
            return pl.DataFrame(schema=schema)

        # Compute matched SE for difference of two sample means
        per_surf_stats = (
            scoped.filter(
                pl.col("surface_profile").is_in([SURFACE_GRIPPY, SURFACE_SKIDDY])
            )
            .group_by(["player_sk", "surface_profile"])
            .agg(
                pl.len().alias("n"),
                pl.col("actual_score").sum().cast(pl.Int64).alias("wins"),
                pl.col("expected_win_prob").sum().alias("exp_wins"),
                (pl.col("residual") * 100.0).var(ddof=1).alias("var_pp"),
            )
        )
        g_stats = per_surf_stats.filter(
            pl.col("surface_profile") == SURFACE_GRIPPY
        ).select(
            "player_sk",
            pl.col("n").alias("g_n"),
            pl.col("wins").alias("g_w"),
            pl.col("exp_wins").alias("g_ew"),
            pl.col("var_pp").alias("g_var"),
        )
        s_stats = per_surf_stats.filter(
            pl.col("surface_profile") == SURFACE_SKIDDY
        ).select(
            "player_sk",
            pl.col("n").alias("s_n"),
            pl.col("wins").alias("s_w"),
            pl.col("exp_wins").alias("s_ew"),
            pl.col("var_pp").alias("s_var"),
        )
        merged = contrasts.join(g_stats, on="player_sk").join(s_stats, on="player_sk")
        rows: list[dict[str, object]] = []
        for r in merged.to_dicts():
            gn, sn = int(r["g_n"]), int(r["s_n"])
            gw, sw = int(r["g_w"]), int(r["s_w"])
            tot_m = gn + sn
            tot_w = gw + sw
            tot_l = tot_m - tot_w
            g_var = float(r["g_var"] or 0.0)
            s_var = float(r["s_var"] or 0.0)
            se_diff = math.sqrt(max(0.0, g_var / gn + s_var / sn))
            ci95 = _Z_95 * se_diff
            edge = float(r["tilt_pp"])
            rows.append(
                {
                    "rank": 0,
                    "player_sk": int(r["player_sk"]),
                    "flag_url": r["flag_url"],
                    "player_name": str(r["player_name"]),
                    "org": str(r["org"]),
                    "is_chn": bool(r["is_chn"]),
                    "matches": tot_m,
                    "actual_wl": f"{tot_w}-{tot_l}",
                    "wins": tot_w,
                    "losses": tot_l,
                    "expected_wins": float(r["g_ew"]) + float(r["s_ew"]),
                    "win_pct_over_expected": edge,
                    "ci95_pp": ci95,
                    "abs_edge": abs(edge),
                }
            )
        out = pl.DataFrame(rows, schema=schema)
    else:
        if target_scope in {SURFACE_GRIPPY, SURFACE_SKIDDY, SURFACE_OTHER}:
            scoped = scoped.filter(pl.col("surface_profile") == target_scope)
        else:
            scoped = scoped.filter(pl.col("table_sponsor") == target_scope)

        if scoped.is_empty():
            return pl.DataFrame(schema=schema)

        agg = (
            scoped.group_by(["player_sk", "flag_url", "player_name", "org", "is_chn"])
            .agg(
                pl.len().cast(pl.Int64).alias("matches"),
                (pl.col("actual_score") == 1.0).sum().cast(pl.Int64).alias("wins"),
                (pl.col("actual_score") == 0.0).sum().cast(pl.Int64).alias("losses"),
                pl.col("expected_win_prob").sum().alias("expected_wins"),
                (pl.col("residual").mean() * 100.0).alias("win_pct_over_expected"),
                pl.col("residual").alias("_resids"),
            )
            .filter(pl.col("matches") >= min_matches)
        )
        if agg.is_empty():
            return pl.DataFrame(schema=schema)

        rows = []
        for r in agg.to_dicts():
            ci95_val = residual_ci95_pp([float(x) for x in r["_resids"]])
            edge = float(r["win_pct_over_expected"])
            w, lo = int(r["wins"]), int(r["losses"])
            rows.append(
                {
                    "rank": 0,
                    "player_sk": int(r["player_sk"]),
                    "flag_url": r["flag_url"],
                    "player_name": str(r["player_name"]),
                    "org": str(r["org"]),
                    "is_chn": bool(r["is_chn"]),
                    "matches": int(r["matches"]),
                    "actual_wl": f"{w}-{lo}",
                    "wins": w,
                    "losses": lo,
                    "expected_wins": float(r["expected_wins"]),
                    "win_pct_over_expected": edge,
                    "ci95_pp": ci95_val,
                    "abs_edge": abs(edge),
                }
            )
        out = pl.DataFrame(rows, schema=schema)

    if out.is_empty():
        return out

    if sort_mode == "Underperformers":
        sorted_df = out.sort(
            ["win_pct_over_expected", "matches", "player_sk"],
            descending=[False, True, False],
        )
    elif sort_mode == "Largest Magnitude":
        sorted_df = out.sort(
            ["abs_edge", "matches", "player_sk"],
            descending=[True, True, False],
        )
    else:
        sorted_df = out.sort(
            ["win_pct_over_expected", "matches", "player_sk"],
            descending=[True, True, False],
        )

    return sorted_df.with_columns(
        pl.int_range(1, pl.len() + 1, dtype=pl.Int64).alias("rank")
    ).select(list(schema.keys()))


def _friendly_surface_label(label: str) -> str:
    mapping = {
        SURFACE_GRIPPY: "Asian Tables (Grippy)",
        SURFACE_SKIDDY: "European Tables (Skiddy)",
        SURFACE_OTHER: "Other Tables",
    }
    return mapping.get(label, label)


def _friendly_cohort_name(cohort_name: str) -> str:
    if cohort_name == "China (CHN vs Non-CHN)":
        return "Chinese Players (vs. Rest of World)"
    if cohort_name.startswith("China"):
        return "Chinese Players (All Matches)"
    return "Non-Chinese Players"


def build_residual_forest_plot(
    cohort_df: pl.DataFrame,
    *,
    title: str = "Do Chinese Players Win More Often Than Expected?",
    pool_label: str = "",
) -> go.Figure:
    """Build horizontal dot-and-whisker forest plot comparing China vs Non-China."""
    fig = go.Figure()
    if cohort_df.is_empty():
        return style_figure(fig, height=360, legend_bottom=True)

    groups_ordered: list[str] = []
    for g in cohort_df["group_label"].to_list():
        if g not in groups_ordered:
            groups_ordered.append(g)
    # Reverse so highest volume / primary surface sits at the top of the horizontal plot
    y_labels = list(reversed(groups_ordered))
    y_idx = {lbl: float(i) for i, lbl in enumerate(y_labels)}

    cohorts: list[str] = []
    for c in cohort_df["cohort"].to_list():
        if c not in cohorts:
            cohorts.append(c)

    offsets = (
        {cohorts[0]: 0.14, cohorts[1]: -0.14}
        if len(cohorts) == 2
        else dict.fromkeys(cohorts, 0.0)
    )

    all_bounds: list[float] = [0.0]
    right_annotations: list[tuple[float, str]] = []

    for cohort_name in cohorts:
        sub = cohort_df.filter(pl.col("cohort") == cohort_name)
        is_chn_cohort = cohort_name.startswith("China")
        color = COLOR_CHN if is_chn_cohort else COLOR_NON_CHN
        display_cohort = _friendly_cohort_name(cohort_name)

        xs: list[float] = []
        ys: list[float] = []
        errs: list[float] = []
        customdata: list[tuple[str, int, float, float, float]] = []

        for r in sub.to_dicts():
            lbl = str(r["group_label"])
            friendly_lbl = _friendly_surface_label(lbl)
            y_pos = y_idx[lbl] + offsets[cohort_name]
            val = float(r["residual_pp"])
            ci = float(r["ci95_pp"]) if r["ci95_pp"] is not None else 0.0
            xs.append(val)
            ys.append(y_pos)
            errs.append(ci)
            all_bounds.extend([val - ci, val + ci])
            customdata.append(
                (
                    friendly_lbl,
                    int(r["n"]),
                    float(r["actual_win_pct"]),
                    float(r["expected_win_pct"]),
                    ci,
                )
            )
            right_annotations.append((y_pos, f"n={int(r['n']):,}"))

        fig.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                error_x={
                    "type": "data",
                    "array": errs,
                    "visible": True,
                    "thickness": 1.8,
                    "width": 5,
                },
                mode="markers",
                name=display_cohort,
                marker={
                    "color": color,
                    "size": 10 if is_chn_cohort else 9,
                    "symbol": "diamond" if is_chn_cohort else "circle",
                    "line": {"width": 1.0, "color": "rgba(255,255,255,0.55)"},
                },
                customdata=customdata,
                hovertemplate=(
                    "<b>%{customdata[0]}</b> · " + display_cohort + "<br>"
                    "vs. Expected: <b>%{x:+.1f}%</b> (±%{customdata[4]:.1f}%)<br>"
                    "Won %{customdata[2]:.1f}% of matches "
                    "(expected %{customdata[3]:.1f}%)<br>"
                    "Matches: %{customdata[1]:,}<extra></extra>"
                ),
            )
        )

    fig.add_vline(
        x=0.0,
        line_dash="dash",
        line_width=1.4,
        line_color="rgba(128, 128, 128, 0.7)",
    )

    x_min = min(all_bounds) - 2.2
    x_max = max(all_bounds) + 2.2
    x_span = max(8.0, x_max - x_min)
    x_right_pad = x_max + 0.12 * x_span

    for y_pos, txt in right_annotations:
        fig.add_annotation(
            x=1.01,
            xref="paper",
            y=y_pos,
            yref="y",
            text=txt,
            showarrow=False,
            xanchor="left",
            font={"size": 11, "color": "rgba(148, 163, 184, 0.95)"},
        )

    full_title = (
        f"{pool_label}: {title}"
        if pool_label and not title.startswith(pool_label)
        else title
    )
    height = max(320, 72 * len(y_labels) + 120)
    fig.update_layout(
        title=full_title,
        xaxis_title=(
            "Win Rate vs. Expected  "
            "(\u2190 Worse than expected  |  Better than expected \u2192)"
        ),
        xaxis={"range": [min(-5.0, x_min), max(5.0, x_right_pad)], "ticksuffix": "%"},
        yaxis={
            "tickmode": "array",
            "tickvals": list(range(len(y_labels))),
            "ticktext": [_friendly_surface_label(lbl) for lbl in y_labels],
            "range": [-0.55, len(y_labels) - 0.45],
        },
    )
    styled = style_figure(fig, height=height, legend_bottom=True)
    styled.update_layout(margin={"l": 155, "r": 78, "t": 60, "b": 95})
    return styled


def build_tilt_comparison_charts(
    tilt: TiltAnalysisResult,
    *,
    pool_label: str = "",
) -> tuple[go.Figure, go.Figure]:
    """Build (1) % CHN by Player Tilt Group and (2) Mean Tilt CHN vs Non-CHN."""
    fig_share = go.Figure()
    fig_diff = go.Figure()

    if tilt.contingency_df.is_empty():
        return (
            style_figure(fig_share, height=340, legend_bottom=False),
            style_figure(fig_diff, height=340, legend_bottom=False),
        )

    prefix = f"{pool_label}: " if pool_label else ""
    c_rows = tilt.contingency_df.to_dicts()
    x_lbls = [
        (
            "Play Better on<br>Asian Tables"
            if "Grippy" in str(r["tilt_group"])
            else "Play Better on<br>European Tables"
        )
        + f"<br>(n={int(r['total_players']):,})"
        for r in c_rows
    ]
    pcts = [float(r["pct_chn"]) for r in c_rows]
    err_plus = [float(r["pct_chn_ci_high"]) - float(r["pct_chn"]) for r in c_rows]
    err_minus = [float(r["pct_chn"]) - float(r["pct_chn_ci_low"]) for r in c_rows]
    colors = [COLOR_GRIPPY, COLOR_SKIDDY]

    fig_share.add_trace(
        go.Bar(
            x=x_lbls,
            y=pcts,
            error_y={
                "type": "data",
                "symmetric": False,
                "array": err_plus,
                "arrayminus": err_minus,
                "visible": True,
            },
            marker_color=colors,
            text=[
                f"{p:.1f}% ({int(r['chn_players'])}/{int(r['total_players'])})"
                for p, r in zip(pcts, c_rows, strict=True)
            ],
            textposition="outside",
            showlegend=False,
            hovertemplate=("%{x}<br>Share from China: <b>%{y:.1f}%</b><extra></extra>"),
        )
    )
    y_top_share = max([p + ep for p, ep in zip(pcts, err_plus, strict=True)] + [10.0])
    fig_share.update_layout(
        title=f"{prefix}% of Players Who Are Chinese",
        yaxis_title="% of Group from China",
        yaxis={"range": [0.0, min(100.0, y_top_share * 1.28 + 2.0)], "ticksuffix": "%"},
    )

    # Chart 2: Mean (Grippy - Skiddy) boost for CHN vs Non-CHN
    groups = [
        (
            "Chinese Players",
            tilt.chn_mean_tilt_pp,
            tilt.chn_tilt_ci95_pp,
            COLOR_CHN,
        ),
        (
            "Non-Chinese Players",
            tilt.non_chn_mean_tilt_pp,
            tilt.non_chn_tilt_ci95_pp,
            COLOR_NON_CHN,
        ),
    ]
    valid_groups = [(lbl, m, ci, col) for lbl, m, ci, col in groups if m is not None]
    if valid_groups:
        g_x = [lbl for lbl, _, _, _ in valid_groups]
        g_y = [float(m) for _, m, _, _ in valid_groups if m is not None]
        g_ci = [float(ci) if ci is not None else 0.0 for _, _, ci, _ in valid_groups]
        g_col = [col for _, _, _, col in valid_groups]

        fig_diff.add_trace(
            go.Bar(
                x=g_x,
                y=g_y,
                error_y={"type": "data", "array": g_ci, "visible": True},
                marker_color=g_col,
                text=[f"{v:+.1f}%" for v in g_y],
                textposition="outside",
                showlegend=False,
                hovertemplate=(
                    "%{x}<br>Average Asian-table boost: <b>%{y:+.1f}%</b>"
                    " (±%{error_y.array:.1f}%)<extra></extra>"
                ),
            )
        )
        fig_diff.add_hline(y=0.0, line_width=1.2, line_color="rgba(128,128,128,0.6)")
        lo_b = min(m - c for m, c in zip(g_y, g_ci, strict=True))
        hi_b = max(m + c for m, c in zip(g_y, g_ci, strict=True))
        pad = max(1.5, 0.3 * max(abs(lo_b), abs(hi_b), 1.0))
        fig_diff.update_layout(
            title=f"{prefix}Average Boost on Asian vs. European Tables",
            yaxis_title="Extra Win % on Asian Tables",
            yaxis={
                "range": [min(0.0, lo_b) - pad, max(0.0, hi_b) + pad],
                "ticksuffix": "%",
            },
        )

    return (
        style_figure(fig_share, height=350, legend_bottom=False),
        style_figure(fig_diff, height=350, legend_bottom=False),
    )


def build_upset_residual_chart(
    matrix_df: pl.DataFrame,
    *,
    title: str = "Are Upsets More or Less Common Than Expected?",
    pool_label: str = "",
) -> go.Figure:
    """Build horizontal dot-and-whisker chart of upset residuals by brand/surface."""
    fig = go.Figure()
    valid = matrix_df.filter(pl.col("upset_residual_pp").is_not_null())
    if valid.is_empty():
        return style_figure(fig, height=340, legend_bottom=True)

    rows = list(reversed(valid.to_dicts()))
    all_bounds: list[float] = [0.0]

    for profile in SURFACE_ORDER:
        prof_rows = [r for r in rows if r["surface_profile"] == profile]
        if not prof_rows:
            continue
        xs = [float(r["upset_residual_pp"]) for r in prof_rows]
        ys = [_friendly_surface_label(str(r["group_name"])) for r in prof_rows]
        cis = [
            float(r["upset_ci95_pp"]) if r["upset_ci95_pp"] is not None else 0.0
            for r in prof_rows
        ]
        for x, ci in zip(xs, cis, strict=True):
            all_bounds.extend([x - ci, x + ci])

        customdata = [
            (
                int(r["underdog_40_matches"]),
                float(r["upset_rate_pct"]),
                float(r["expected_upset_pct"]),
                ci,
            )
            for r, ci in zip(prof_rows, cis, strict=True)
        ]

        fig.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                error_x={
                    "type": "data",
                    "array": cis,
                    "visible": True,
                    "thickness": 1.8,
                },
                mode="markers",
                name=_friendly_surface_label(profile),
                marker={
                    "color": SURFACE_COLORS.get(profile, COLOR_OTHER),
                    "size": 10,
                    "line": {"width": 1.0, "color": "rgba(255,255,255,0.55)"},
                },
                customdata=customdata,
                hovertemplate=(
                    "<b>%{y}</b><br>"
                    "Upsets vs. expected: <b>%{x:+.1f}%</b> "
                    "(±%{customdata[3]:.1f}%)<br>"
                    "Underdogs won %{customdata[1]:.1f}% "
                    "(expected %{customdata[2]:.1f}%)<br>"
                    "Underdog matches: %{customdata[0]:,}<extra></extra>"
                ),
            )
        )

    fig.add_vline(
        x=0.0,
        line_dash="dash",
        line_width=1.4,
        line_color="rgba(128, 128, 128, 0.7)",
    )
    for r in rows:
        fig.add_annotation(
            x=1.01,
            xref="paper",
            y=_friendly_surface_label(str(r["group_name"])),
            yref="y",
            text=f"n={int(r['underdog_40_matches']):,}",
            showarrow=False,
            xanchor="left",
            font={"size": 11, "color": "rgba(148, 163, 184, 0.95)"},
        )

    full_title = (
        f"{pool_label}: {title}"
        if pool_label and not title.startswith(pool_label)
        else title
    )
    y_order = [_friendly_surface_label(str(r["group_name"])) for r in rows]
    x_min = min(all_bounds) - 1.5
    x_max = max(all_bounds) + 1.5
    height = max(300, 44 * len(rows) + 120)
    fig.update_layout(
        title=full_title,
        xaxis_title=(
            "Upset Rate vs. Expected  (\u2190 Fewer upsets  |  More upsets \u2192)"
        ),
        xaxis={"range": [min(-4.0, x_min), max(4.0, x_max)], "ticksuffix": "%"},
        yaxis={"categoryorder": "array", "categoryarray": y_order},
    )
    styled = style_figure(fig, height=height, legend_bottom=True)
    styled.update_layout(margin={"l": 155, "r": 78, "t": 60, "b": 95})
    return styled

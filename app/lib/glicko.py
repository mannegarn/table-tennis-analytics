"""Glicko-2 ratings analytics, flag/headshot helpers, and matchup calculations.

This module is pure Python + Polars with no Streamlit UI calls so leaderboard
aggregations, time-travel filtering, player rating histories, and head-to-head
predictors can be unit-tested directly.
"""

from __future__ import annotations

import base64
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlsplit, urlunsplit

import polars as pl

GLICKO2_SCALE = 173.7178
APP_DIR = Path(__file__).resolve().parents[1]
PLACEHOLDERS_DIR = APP_DIR / "assets" / "placeholders"

_PLACEHOLDER_EXTS: tuple[tuple[str, str], ...] = (
    (".png", "image/png"),
    (".webp", "image/webp"),
    (".jpg", "image/jpeg"),
    (".jpeg", "image/jpeg"),
    (".svg", "image/svg+xml"),
)

_FALLBACK_SVG_DATA_URI = "data:image/svg+xml;base64," + base64.b64encode(
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80">'
    b'<circle cx="40" cy="40" r="39" fill="#312E81"/>'
    b'<circle cx="40" cy="30" r="13" fill="#DDD6FE"/>'
    b'<path d="M18 68c3-13 12-20 22-20s19 7 22 20" fill="#DDD6FE"/>'
    b"</svg>"
).decode("ascii")

# ITTF / IOC 3-letter codes -> ISO 3166-1 alpha-2 (or UK subdivision) for flagcdn.
IOC_TO_ISO2: dict[str, str] = {
    "AFG": "af",
    "ALB": "al",
    "ALG": "dz",
    "ANG": "ao",
    "ANT": "ag",
    "ARG": "ar",
    "ARM": "am",
    "AUS": "au",
    "AUT": "at",
    "AZE": "az",
    "BAH": "bs",
    "BAN": "bd",
    "BAR": "bb",
    "BDI": "bi",
    "BEL": "be",
    "BEN": "bj",
    "BHU": "bt",
    "BIH": "ba",
    "BLR": "by",
    "BOL": "bo",
    "BOT": "bw",
    "BRA": "br",
    "BRN": "bh",
    "BUL": "bg",
    "CAF": "cf",
    "CAM": "kh",
    "CAN": "ca",
    "CGO": "cg",
    "CHI": "cl",
    "CHN": "cn",
    "CIV": "ci",
    "CMR": "cm",
    "COD": "cd",
    "COK": "ck",
    "COL": "co",
    "CRC": "cr",
    "CRO": "hr",
    "CUB": "cu",
    "CYP": "cy",
    "CZE": "cz",
    "DEN": "dk",
    "DJI": "dj",
    "DMA": "dm",
    "DOM": "do",
    "ECU": "ec",
    "EGY": "eg",
    "ENG": "gb-eng",
    "ESA": "sv",
    "ESP": "es",
    "EST": "ee",
    "ETH": "et",
    "FIJ": "fj",
    "FIN": "fi",
    "FRA": "fr",
    "GAB": "ga",
    "GBR": "gb",
    "GEO": "ge",
    "GER": "de",
    "GHA": "gh",
    "GRE": "gr",
    "GRL": "gl",
    "GUA": "gt",
    "GUY": "gy",
    "HKG": "hk",
    "HON": "hn",
    "HUN": "hu",
    "INA": "id",
    "IND": "in",
    "IRI": "ir",
    "IRL": "ie",
    "IRQ": "iq",
    "ISL": "is",
    "ISR": "il",
    "ITA": "it",
    "JAM": "jm",
    "JEY": "je",
    "JOR": "jo",
    "JPN": "jp",
    "KAZ": "kz",
    "KEN": "ke",
    "KGZ": "kg",
    "KOR": "kr",
    "KOS": "xk",
    "KSA": "sa",
    "KUW": "kw",
    "LAO": "la",
    "LAT": "lv",
    "LBA": "ly",
    "LBN": "lb",
    "LCA": "lc",
    "LES": "ls",
    "LTU": "lt",
    "LUX": "lu",
    "MAC": "mo",
    "MAD": "mg",
    "MAR": "ma",
    "MAS": "my",
    "MAW": "mw",
    "MDA": "md",
    "MDV": "mv",
    "MEX": "mx",
    "MGL": "mn",
    "MHL": "mh",
    "MKD": "mk",
    "MLT": "mt",
    "MNE": "me",
    "MON": "mc",
    "MRI": "mu",
    "MTN": "mr",
    "MYA": "mm",
    "NAM": "na",
    "NCA": "ni",
    "NCL": "nc",
    "NED": "nl",
    "NEP": "np",
    "NGR": "ng",
    "NIR": "gb-nir",
    "NOR": "no",
    "NZL": "nz",
    "OMA": "om",
    "PAK": "pk",
    "PAN": "pa",
    "PAR": "py",
    "PER": "pe",
    "PHI": "ph",
    "PLE": "ps",
    "PNG": "pg",
    "POL": "pl",
    "POR": "pt",
    "PRK": "kp",
    "PUR": "pr",
    "PYF": "pf",
    "QAT": "qa",
    "ROU": "ro",
    "RSA": "za",
    "RUS": "ru",
    "RWA": "rw",
    "SCO": "gb-sct",
    "SEN": "sn",
    "SEY": "sc",
    "SGP": "sg",
    "SLO": "si",
    "SMR": "sm",
    "SOL": "sb",
    "SRB": "rs",
    "SRI": "lk",
    "SUD": "sd",
    "SUI": "ch",
    "SVK": "sk",
    "SWE": "se",
    "SYR": "sy",
    "TAN": "tz",
    "TGA": "to",
    "THA": "th",
    "TOG": "tg",
    "TPE": "tw",
    "TTO": "tt",
    "TUN": "tn",
    "TUR": "tr",
    "UAE": "ae",
    "UGA": "ug",
    "UKR": "ua",
    "URU": "uy",
    "USA": "us",
    "UZB": "uz",
    "VAN": "vu",
    "VEN": "ve",
    "VIE": "vn",
    "WAL": "gb-wls",
    "WLF": "wf",
    "YEM": "ye",
    "ZAM": "zm",
    "ZIM": "zw",
}

NEUTRAL_ORG_CODES: frozenset[str] = frozenset({"AIN", "EOR", "ITT", "REF", "WTT"})


@dataclass(frozen=True)
class HeadToHeadSummary:
    """Computed Glicko-2 matchup odds and historical meetings between two players."""

    p1_player_sk: int
    p2_player_sk: int
    p1_name: str
    p2_name: str
    p1_rating: float | None
    p1_rd: float | None
    p2_rating: float | None
    p2_rd: float | None
    rating_diff: float | None
    p1_win_prob: float | None
    p2_win_prob: float | None
    total_matches: int
    p1_wins: int
    p2_wins: int
    draws: int
    matches_df: pl.DataFrame


def flag_url_for_code(code: str | None, *, width: int = 40) -> str | None:
    """Return a FlagCDN PNG URL for an IOC/ITTF 3-letter code, or ``None``."""
    if not code:
        return None
    norm = code.strip().upper()
    if not norm or norm in NEUTRAL_ORG_CODES:
        return None
    iso2 = IOC_TO_ISO2.get(norm)
    if iso2 is None:
        return None
    return f"https://flagcdn.com/w{width}/{iso2}.png"


@lru_cache(maxsize=16)
def _encode_file_data_uri(
    path_str: str,
    mime: str,
    mtime_ns: int,
    size: int,
) -> str:
    _ = (mtime_ns, size)
    encoded = base64.b64encode(Path(path_str).read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def resolve_placeholder_data_uri(
    gender: str | None = None,
    *,
    theme: Literal["dark", "light"] = "dark",
    placeholders_dir: Path | None = None,
) -> str:
    """Return a base64 data URI for the best matching placeholder headshot."""
    directory = placeholders_dir or PLACEHOLDERS_DIR
    norm_gender = (gender or "").strip().lower()
    stems: list[str] = []
    if norm_gender in {"m", "men", "male"}:
        stems.extend([f"men_{theme}", "men_dark", "men_light", "player_men"])
    elif norm_gender in {"f", "women", "female"}:
        stems.extend([f"women_{theme}", "women_dark", "women_light", "player_women"])
    stems.append("player_default")

    for stem in stems:
        for ext, mime in _PLACEHOLDER_EXTS:
            candidate = directory / f"{stem}{ext}"
            if candidate.is_file():
                stat = candidate.stat()
                return _encode_file_data_uri(
                    str(candidate),
                    mime,
                    stat.st_mtime_ns,
                    stat.st_size,
                )
    return _FALLBACK_SVG_DATA_URI


def sanitize_headshot_url(
    url: str | None,
    *,
    gender: str | None = None,
    theme: Literal["dark", "light"] = "dark",
    placeholder_uri: str | None = None,
    placeholders_dir: Path | None = None,
) -> str:
    """Return a URL-encoded headshot URL, or a placeholder data URI if missing."""
    if url:
        cleaned = url.strip()
        if cleaned:
            parts = urlsplit(cleaned)
            if parts.scheme in {"http", "https"} and parts.netloc:
                encoded_path = quote(parts.path, safe="/:@!$&'()*+,;=-._~%")
                return urlunsplit(
                    (
                        parts.scheme,
                        parts.netloc,
                        encoded_path,
                        parts.query,
                        parts.fragment,
                    )
                )
    if placeholder_uri is not None:
        return placeholder_uri
    return resolve_placeholder_data_uri(
        gender,
        theme=theme,
        placeholders_dir=placeholders_dir,
    )


def glicko2_win_probability(
    r1: float,
    rd1: float,
    r2: float,
    rd2: float,
    *,
    base_rating: float = 1500.0,
) -> float:
    """Return Glicko-2 expected win probability for player 1 against player 2."""
    mu1 = (r1 - base_rating) / GLICKO2_SCALE
    mu2 = (r2 - base_rating) / GLICKO2_SCALE
    phi_combined = math.hypot(rd1 / GLICKO2_SCALE, rd2 / GLICKO2_SCALE)
    g_val = 1.0 / math.sqrt(1.0 + 3.0 * phi_combined * phi_combined / (math.pi**2))
    exponent = -g_val * (mu1 - mu2)
    if exponent > 700.0:
        return 0.0
    if exponent < -700.0:
        return 1.0
    return 1.0 / (1.0 + math.exp(exponent))


def _empty_tall_ratings() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "player_sk": pl.Int64,
            "opponent_player_sk": pl.Int64,
            "match_sk": pl.Int64,
            "event_sk": pl.Int64,
            "gender_category": pl.String,
            "rating_date": pl.Date,
            "player_side": pl.Int8,
            "pre_rating": pl.Float64,
            "pre_rd": pl.Float64,
            "pre_vol": pl.Float64,
            "post_rating": pl.Float64,
            "post_rd": pl.Float64,
            "post_vol": pl.Float64,
            "rating_delta": pl.Float64,
            "expected_win_prob": pl.Float64,
            "actual_score": pl.Float64,
        }
    )


def unpivot_player_ratings(ratings: pl.DataFrame) -> pl.DataFrame:
    """Convert 1-row-per-match ratings into a 2-rows-per-match tall DataFrame."""
    if ratings.is_empty():
        return _empty_tall_ratings()

    p1 = ratings.select(
        pl.col("p1_player_sk").cast(pl.Int64).alias("player_sk"),
        pl.col("p2_player_sk").cast(pl.Int64).alias("opponent_player_sk"),
        pl.col("match_sk").cast(pl.Int64),
        pl.col("event_sk").cast(pl.Int64),
        pl.col("gender_category").cast(pl.String),
        pl.col("rating_date").cast(pl.Date),
        pl.lit(1, dtype=pl.Int8).alias("player_side"),
        pl.col("p1_pre_rating").cast(pl.Float64).alias("pre_rating"),
        pl.col("p1_pre_rd").cast(pl.Float64).alias("pre_rd"),
        pl.col("p1_pre_vol").cast(pl.Float64).alias("pre_vol"),
        pl.col("p1_post_rating").cast(pl.Float64).alias("post_rating"),
        pl.col("p1_post_rd").cast(pl.Float64).alias("post_rd"),
        pl.col("p1_post_vol").cast(pl.Float64).alias("post_vol"),
        (pl.col("p1_post_rating") - pl.col("p1_pre_rating"))
        .cast(pl.Float64)
        .alias("rating_delta"),
        pl.col("p1_expected_win_prob").cast(pl.Float64).alias("expected_win_prob"),
        pl.col("p1_actual_score").cast(pl.Float64).alias("actual_score"),
    )
    p2 = ratings.select(
        pl.col("p2_player_sk").cast(pl.Int64).alias("player_sk"),
        pl.col("p1_player_sk").cast(pl.Int64).alias("opponent_player_sk"),
        pl.col("match_sk").cast(pl.Int64),
        pl.col("event_sk").cast(pl.Int64),
        pl.col("gender_category").cast(pl.String),
        pl.col("rating_date").cast(pl.Date),
        pl.lit(2, dtype=pl.Int8).alias("player_side"),
        pl.col("p2_pre_rating").cast(pl.Float64).alias("pre_rating"),
        pl.col("p2_pre_rd").cast(pl.Float64).alias("pre_rd"),
        pl.col("p2_pre_vol").cast(pl.Float64).alias("pre_vol"),
        pl.col("p2_post_rating").cast(pl.Float64).alias("post_rating"),
        pl.col("p2_post_rd").cast(pl.Float64).alias("post_rd"),
        pl.col("p2_post_vol").cast(pl.Float64).alias("post_vol"),
        (pl.col("p2_post_rating") - pl.col("p2_pre_rating"))
        .cast(pl.Float64)
        .alias("rating_delta"),
        (1.0 - pl.col("p1_expected_win_prob"))
        .cast(pl.Float64)
        .alias("expected_win_prob"),
        (1.0 - pl.col("p1_actual_score")).cast(pl.Float64).alias("actual_score"),
    )
    return pl.concat([p1, p2], how="vertical")


def _clean_attr_expr(col_name: str, alias: str) -> pl.Expr:
    cleaned = pl.col(col_name).cast(pl.String).str.strip_chars()
    return (
        pl.when(cleaned.is_null() | (cleaned == ""))
        .then(pl.lit("Unknown"))
        .otherwise(cleaned)
        .alias(alias)
    )


def _empty_leaderboard() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "rank": pl.Int64,
            "player_sk": pl.Int64,
            "photo_url": pl.String,
            "flag_url": pl.String,
            "org": pl.String,
            "country_name": pl.String,
            "player_name": pl.String,
            "rating": pl.Float64,
            "rd": pl.Float64,
            "vol": pl.Float64,
            "peak_rating": pl.Float64,
            "peak_date": pl.Date,
            "peak_display": pl.String,
            "avg_rating": pl.Float64,
            "trend_delta": pl.Float64,
            "matches": pl.Int64,
            "wins": pl.Int64,
            "losses": pl.Int64,
            "draws": pl.Int64,
            "win_pct": pl.Float64,
            "style": pl.String,
            "grip": pl.String,
            "hand": pl.String,
            "age": pl.Int64,
            "last_match_date": pl.Date,
        }
    )


def build_glicko_leaderboard(
    ratings: pl.DataFrame,
    players: pl.DataFrame,
    *,
    gender: Literal["men", "women"],
    as_of_date: date | None = None,
    max_rd: float | None = None,
    min_matches: int = 1,
    trend_days: int = 90,
    theme: Literal["dark", "light"] = "dark",
    placeholders_dir: Path | None = None,
) -> pl.DataFrame:
    """Build a ranked Glicko-2 leaderboard with peak/avg/trend, flags, and photos."""
    scoped = ratings.filter(pl.col("gender_category") == gender)
    if as_of_date is not None:
        scoped = scoped.filter(
            pl.col("rating_date").is_not_null() & (pl.col("rating_date") <= as_of_date)
        )

    tall = unpivot_player_ratings(scoped)
    if tall.is_empty():
        return _empty_leaderboard()

    tall = tall.sort(["rating_date", "match_sk"], nulls_last=False)

    max_date_raw = tall["rating_date"].drop_nulls().max()
    anchor_date: date | None = as_of_date or (
        max_date_raw if isinstance(max_date_raw, date) else None
    )

    core = tall.group_by("player_sk").agg(
        pl.col("post_rating").last().alias("rating"),
        pl.col("post_rd").last().alias("rd"),
        pl.col("post_vol").last().alias("vol"),
        pl.col("rating_date").last().alias("last_match_date"),
        pl.col("post_rating").max().alias("peak_rating"),
        pl.col("rating_date")
        .sort_by(["post_rating", "rating_date"], descending=[True, False])
        .first()
        .alias("peak_date"),
        pl.col("post_rating").mean().alias("avg_rating"),
        pl.len().cast(pl.Int64).alias("matches"),
        (pl.col("actual_score") == 1.0).sum().cast(pl.Int64).alias("wins"),
        (pl.col("actual_score") == 0.0).sum().cast(pl.Int64).alias("losses"),
        (pl.col("actual_score") == 0.5).sum().cast(pl.Int64).alias("draws"),
    )

    if anchor_date is not None and trend_days > 0:
        cutoff_date = anchor_date - timedelta(days=trend_days)
        dated = tall.filter(pl.col("rating_date").is_not_null())
        baseline_before = (
            dated.filter(pl.col("rating_date") <= cutoff_date)
            .group_by("player_sk")
            .agg(pl.col("post_rating").last().alias("baseline_before"))
        )
        window_agg = (
            dated.filter(pl.col("rating_date") > cutoff_date)
            .group_by("player_sk")
            .agg(
                pl.col("pre_rating").first().alias("first_pre_in_window"),
                pl.len().alias("matches_in_window"),
            )
        )
        core = (
            core.join(baseline_before, on="player_sk", how="left")
            .join(window_agg, on="player_sk", how="left")
            .with_columns(
                pl.when(pl.col("matches_in_window").fill_null(0) == 0)
                .then(pl.lit(0.0))
                .otherwise(
                    pl.col("rating")
                    - pl.coalesce(
                        [pl.col("baseline_before"), pl.col("first_pre_in_window")]
                    )
                )
                .cast(pl.Float64)
                .alias("trend_delta")
            )
            .drop(["baseline_before", "first_pre_in_window", "matches_in_window"])
        )
    else:
        core = core.with_columns(pl.lit(0.0).alias("trend_delta"))

    core = core.with_columns(
        (
            100.0
            * (pl.col("wins").cast(pl.Float64) + 0.5 * pl.col("draws").cast(pl.Float64))
            / pl.col("matches").cast(pl.Float64)
        ).alias("win_pct")
    ).filter(pl.col("matches") >= min_matches)

    if max_rd is not None:
        core = core.filter(pl.col("rd") <= max_rd)

    if core.is_empty():
        return _empty_leaderboard()

    org_expr = (
        pl.coalesce([pl.col("organization_code"), pl.col("country_code")])
        .fill_null("")
        .cast(pl.String)
        .str.strip_chars()
    )
    country_sources: list[pl.Expr] = []
    if "organization_name" in players.columns:
        country_sources.append(
            pl.col("organization_name").cast(pl.String).str.strip_chars()
        )
    if "country_name" in players.columns:
        country_sources.append(pl.col("country_name").cast(pl.String).str.strip_chars())
    country_sources.append(org_expr)

    player_cols = players.select(
        pl.col("player_sk").cast(pl.Int64),
        pl.col("player_name").fill_null("Unknown Player").cast(pl.String),
        org_expr.alias("org"),
        pl.coalesce(country_sources)
        .replace("", None)
        .fill_null("Unknown")
        .cast(pl.String)
        .alias("country_name"),
        _clean_attr_expr("style", "style"),
        _clean_attr_expr("grip", "grip"),
        _clean_attr_expr("hand", "hand"),
        pl.col("age").cast(pl.Int64),
        pl.col("head_shot").cast(pl.String),
        pl.col("gender").cast(pl.String),
    )

    joined = core.join(player_cols, on="player_sk", how="left").with_columns(
        pl.col("player_name").fill_null("Unknown Player"),
        pl.col("org").fill_null(""),
        pl.col("country_name").fill_null("Unknown"),
        pl.col("style").fill_null("Unknown"),
        pl.col("grip").fill_null("Unknown"),
        pl.col("hand").fill_null("Unknown"),
    )

    ranked = joined.sort(
        ["rating", "rd", "matches", "player_sk"],
        descending=[True, False, True, False],
    ).with_columns(pl.int_range(1, pl.len() + 1, dtype=pl.Int64).alias("rank"))

    default_placeholder = resolve_placeholder_data_uri(
        gender,
        theme=theme,
        placeholders_dir=placeholders_dir,
    )
    gender_placeholders = {
        "M": resolve_placeholder_data_uri(
            "M", theme=theme, placeholders_dir=placeholders_dir
        ),
        "F": resolve_placeholder_data_uri(
            "F", theme=theme, placeholders_dir=placeholders_dir
        ),
    }

    flag_urls = [flag_url_for_code(code) for code in ranked["org"].to_list()]
    photo_urls = [
        sanitize_headshot_url(
            url,
            gender=p_gender,
            theme=theme,
            placeholder_uri=gender_placeholders.get(
                (p_gender or "").upper(), default_placeholder
            ),
            placeholders_dir=placeholders_dir,
        )
        for url, p_gender in zip(
            ranked["head_shot"].to_list(),
            ranked["gender"].to_list(),
            strict=True,
        )
    ]

    peak_displays = [
        f"{p_val:.1f} ({p_date:%d/%m/%y})"
        if isinstance(p_date, date)
        else f"{p_val:.1f}"
        for p_val, p_date in zip(
            ranked["peak_rating"].to_list(),
            ranked["peak_date"].to_list(),
            strict=True,
        )
    ]

    return ranked.with_columns(
        pl.Series("flag_url", flag_urls, dtype=pl.String),
        pl.Series("photo_url", photo_urls, dtype=pl.String),
        pl.Series("peak_display", peak_displays, dtype=pl.String),
    ).select(
        "rank",
        "player_sk",
        "photo_url",
        "flag_url",
        "org",
        "country_name",
        "player_name",
        "rating",
        "rd",
        "vol",
        "peak_rating",
        "peak_date",
        "peak_display",
        "avg_rating",
        "trend_delta",
        "matches",
        "wins",
        "losses",
        "draws",
        "win_pct",
        "style",
        "grip",
        "hand",
        "age",
        "last_match_date",
    )


def build_as_of_event_options(
    ratings: pl.DataFrame,
    events: pl.DataFrame,
    *,
    gender: Literal["men", "women"],
) -> list[tuple[str, date | None]]:
    """Return dropdown options ``(label, as_of_date)`` newest-first for a pool."""
    dated = ratings.filter(
        (pl.col("gender_category") == gender) & pl.col("rating_date").is_not_null()
    )
    if dated.is_empty():
        return [("Latest (All matches)", None)]

    max_date_raw = dated["rating_date"].max()
    latest_label = (
        f"Latest ({max_date_raw:%d/%m/%y} — All matches)"
        if isinstance(max_date_raw, date)
        else "Latest (All matches)"
    )
    options: list[tuple[str, date | None]] = [(latest_label, None)]

    per_event = (
        dated.group_by("event_sk")
        .agg(pl.col("rating_date").max().alias("event_last_date"))
        .join(
            events.select(
                pl.col("event_sk").cast(pl.Int64),
                pl.col("event_name").fill_null("Unknown Event").cast(pl.String),
            ),
            on="event_sk",
            how="left",
        )
        .with_columns(pl.col("event_name").fill_null("Unknown Event"))
        .sort(["event_last_date", "event_sk"], descending=[True, True])
    )

    for row in per_event.to_dicts():
        ev_date = row["event_last_date"]
        ev_name = str(row["event_name"])
        if isinstance(ev_date, date):
            options.append((f"{ev_date:%d/%m/%y} · {ev_name}", ev_date))

    return options


def _format_score_expr(
    side_col: str,
    g1_col: str,
    g2_col: str,
) -> pl.Expr:
    return (
        pl.when(pl.col(g1_col).is_not_null() & pl.col(g2_col).is_not_null())
        .then(
            pl.when(pl.col(side_col) == 1)
            .then(
                pl.col(g1_col).cast(pl.String)
                + pl.lit("-")
                + pl.col(g2_col).cast(pl.String)
            )
            .otherwise(
                pl.col(g2_col).cast(pl.String)
                + pl.lit("-")
                + pl.col(g1_col).cast(pl.String)
            )
        )
        .otherwise(pl.lit(None, dtype=pl.String))
        .alias("score")
    )


def build_player_rating_history(
    ratings: pl.DataFrame,
    players: pl.DataFrame,
    events: pl.DataFrame,
    matches: pl.DataFrame,
    player_sks: Sequence[int],
) -> pl.DataFrame:
    """Return chronological match-by-match rating history for selected players."""
    target_sks = [int(sk) for sk in player_sks]
    if not target_sks or ratings.is_empty():
        return pl.DataFrame(
            schema={
                "player_sk": pl.Int64,
                "player_name": pl.String,
                "match_sk": pl.Int64,
                "event_sk": pl.Int64,
                "event_name": pl.String,
                "round_code": pl.String,
                "rating_date": pl.Date,
                "opponent_player_sk": pl.Int64,
                "opponent_name": pl.String,
                "opponent_org": pl.String,
                "result": pl.String,
                "score": pl.String,
                "pre_rating": pl.Float64,
                "post_rating": pl.Float64,
                "post_rd": pl.Float64,
                "rating_delta": pl.Float64,
                "expected_win_prob": pl.Float64,
                "opponent_pre_rating": pl.Float64,
            }
        )

    filtered = ratings.filter(
        pl.col("p1_player_sk").is_in(target_sks)
        | pl.col("p2_player_sk").is_in(target_sks)
    )
    if filtered.is_empty():
        return pl.DataFrame(
            schema={
                "player_sk": pl.Int64,
                "player_name": pl.String,
                "match_sk": pl.Int64,
                "event_sk": pl.Int64,
                "event_name": pl.String,
                "round_code": pl.String,
                "rating_date": pl.Date,
                "opponent_player_sk": pl.Int64,
                "opponent_name": pl.String,
                "opponent_org": pl.String,
                "result": pl.String,
                "score": pl.String,
                "pre_rating": pl.Float64,
                "post_rating": pl.Float64,
                "post_rd": pl.Float64,
                "rating_delta": pl.Float64,
                "expected_win_prob": pl.Float64,
                "opponent_pre_rating": pl.Float64,
            }
        )

    # Carry opponent pre-match rating alongside each side before unpivoting.
    opp_lookup = pl.concat(
        [
            filtered.select(
                pl.col("match_sk").cast(pl.Int64),
                pl.col("p1_player_sk").cast(pl.Int64).alias("player_sk"),
                pl.col("p2_pre_rating").cast(pl.Float64).alias("opponent_pre_rating"),
            ),
            filtered.select(
                pl.col("match_sk").cast(pl.Int64),
                pl.col("p2_player_sk").cast(pl.Int64).alias("player_sk"),
                pl.col("p1_pre_rating").cast(pl.Float64).alias("opponent_pre_rating"),
            ),
        ],
        how="vertical",
    )

    tall = (
        unpivot_player_ratings(filtered)
        .filter(pl.col("player_sk").is_in(target_sks))
        .join(opp_lookup, on=["match_sk", "player_sk"], how="left")
    )

    player_names = players.select(
        pl.col("player_sk").cast(pl.Int64),
        pl.col("player_name").fill_null("Unknown Player").cast(pl.String),
        pl.coalesce([pl.col("organization_code"), pl.col("country_code")])
        .fill_null("")
        .cast(pl.String)
        .alias("_org"),
    )
    event_names = events.select(
        pl.col("event_sk").cast(pl.Int64),
        pl.col("event_name").fill_null("Unknown Event").cast(pl.String),
    )
    match_meta = matches.select(
        pl.col("match_sk").cast(pl.Int64),
        pl.col("round_code").cast(pl.String),
        pl.col("games_won_p1").cast(pl.Int64),
        pl.col("games_won_p2").cast(pl.Int64),
    )

    return (
        tall.join(
            player_names.select("player_sk", "player_name"),
            on="player_sk",
            how="left",
        )
        .join(
            player_names.select(
                pl.col("player_sk").alias("opponent_player_sk"),
                pl.col("player_name").alias("opponent_name"),
                pl.col("_org").alias("opponent_org"),
            ),
            on="opponent_player_sk",
            how="left",
        )
        .join(event_names, on="event_sk", how="left")
        .join(match_meta, on="match_sk", how="left")
        .with_columns(
            pl.col("player_name").fill_null("Unknown Player"),
            pl.col("opponent_name").fill_null("Unknown Player"),
            pl.col("opponent_org").fill_null(""),
            pl.col("event_name").fill_null("Unknown Event"),
            pl.when(pl.col("actual_score") == 1.0)
            .then(pl.lit("W"))
            .when(pl.col("actual_score") == 0.0)
            .then(pl.lit("L"))
            .otherwise(pl.lit("D"))
            .alias("result"),
            _format_score_expr("player_side", "games_won_p1", "games_won_p2"),
        )
        .sort(["player_sk", "rating_date", "match_sk"], nulls_last=False)
        .select(
            "player_sk",
            "player_name",
            "match_sk",
            "event_sk",
            "event_name",
            "round_code",
            "rating_date",
            "opponent_player_sk",
            "opponent_name",
            "opponent_org",
            "result",
            "score",
            "pre_rating",
            "post_rating",
            "post_rd",
            "rating_delta",
            "expected_win_prob",
            "opponent_pre_rating",
        )
    )


def _latest_player_rating(
    ratings: pl.DataFrame,
    player_sk: int,
) -> tuple[float, float] | None:
    tall = (
        unpivot_player_ratings(
            ratings.filter(
                (pl.col("p1_player_sk") == player_sk)
                | (pl.col("p2_player_sk") == player_sk)
            )
        )
        .filter(pl.col("player_sk") == player_sk)
        .sort(["rating_date", "match_sk"], nulls_last=False)
    )
    if tall.is_empty():
        return None
    row = tall.row(-1, named=True)
    return float(row["post_rating"]), float(row["post_rd"])


def build_head_to_head(
    ratings: pl.DataFrame,
    players: pl.DataFrame,
    events: pl.DataFrame,
    matches: pl.DataFrame,
    p1_player_sk: int,
    p2_player_sk: int,
) -> HeadToHeadSummary:
    """Compute Glicko-2 matchup win probability and historical H2H meetings."""
    name_map = {
        int(row["player_sk"]): str(row["player_name"] or "Unknown Player")
        for row in players.filter(
            pl.col("player_sk").is_in([p1_player_sk, p2_player_sk])
        )
        .select("player_sk", "player_name")
        .to_dicts()
    }
    p1_name = name_map.get(p1_player_sk, "Unknown Player")
    p2_name = name_map.get(p2_player_sk, "Unknown Player")

    r1_pair = _latest_player_rating(ratings, p1_player_sk)
    r2_pair = _latest_player_rating(ratings, p2_player_sk)

    p1_rating = r1_pair[0] if r1_pair is not None else None
    p1_rd = r1_pair[1] if r1_pair is not None else None
    p2_rating = r2_pair[0] if r2_pair is not None else None
    p2_rd = r2_pair[1] if r2_pair is not None else None

    rating_diff: float | None = None
    p1_win_prob: float | None = None
    p2_win_prob: float | None = None
    if (
        p1_rating is not None
        and p1_rd is not None
        and p2_rating is not None
        and p2_rd is not None
    ):
        rating_diff = p1_rating - p2_rating
        p1_win_prob = glicko2_win_probability(p1_rating, p1_rd, p2_rating, p2_rd)
        p2_win_prob = 1.0 - p1_win_prob

    direct = ratings.filter(
        (
            (pl.col("p1_player_sk") == p1_player_sk)
            & (pl.col("p2_player_sk") == p2_player_sk)
        )
        | (
            (pl.col("p1_player_sk") == p2_player_sk)
            & (pl.col("p2_player_sk") == p1_player_sk)
        )
    )

    if direct.is_empty():
        empty_h2h = pl.DataFrame(
            schema={
                "match_sk": pl.Int64,
                "rating_date": pl.Date,
                "event_name": pl.String,
                "round_code": pl.String,
                "winner": pl.String,
                "score": pl.String,
                "p1_pre_rating": pl.Float64,
                "p2_pre_rating": pl.Float64,
                "p1_rating_delta": pl.Float64,
                "p2_rating_delta": pl.Float64,
            }
        )
        return HeadToHeadSummary(
            p1_player_sk=p1_player_sk,
            p2_player_sk=p2_player_sk,
            p1_name=p1_name,
            p2_name=p2_name,
            p1_rating=p1_rating,
            p1_rd=p1_rd,
            p2_rating=p2_rating,
            p2_rd=p2_rd,
            rating_diff=rating_diff,
            p1_win_prob=p1_win_prob,
            p2_win_prob=p2_win_prob,
            total_matches=0,
            p1_wins=0,
            p2_wins=0,
            draws=0,
            matches_df=empty_h2h,
        )

    event_names = events.select(
        pl.col("event_sk").cast(pl.Int64),
        pl.col("event_name").fill_null("Unknown Event").cast(pl.String),
    )
    match_meta = matches.select(
        pl.col("match_sk").cast(pl.Int64),
        pl.col("round_code").cast(pl.String),
        pl.col("games_won_p1").cast(pl.Int64),
        pl.col("games_won_p2").cast(pl.Int64),
    )

    # Normalize every direct match so p1_* columns always refer to p1_player_sk.
    normalized = (
        direct.with_columns(
            (pl.col("p1_player_sk") == p1_player_sk).alias("_p1_is_left")
        )
        .join(event_names, on="event_sk", how="left")
        .join(match_meta, on="match_sk", how="left")
        .with_columns(
            pl.col("event_name").fill_null("Unknown Event"),
            pl.when(pl.col("_p1_is_left"))
            .then(pl.col("p1_actual_score"))
            .otherwise(1.0 - pl.col("p1_actual_score"))
            .alias("_p1_score"),
            pl.when(pl.col("_p1_is_left"))
            .then(pl.col("p1_pre_rating"))
            .otherwise(pl.col("p2_pre_rating"))
            .alias("p1_pre_rating"),
            pl.when(pl.col("_p1_is_left"))
            .then(pl.col("p2_pre_rating"))
            .otherwise(pl.col("p1_pre_rating"))
            .alias("p2_pre_rating"),
            pl.when(pl.col("_p1_is_left"))
            .then(pl.col("p1_post_rating") - pl.col("p1_pre_rating"))
            .otherwise(pl.col("p2_post_rating") - pl.col("p2_pre_rating"))
            .alias("p1_rating_delta"),
            pl.when(pl.col("_p1_is_left"))
            .then(pl.col("p2_post_rating") - pl.col("p2_pre_rating"))
            .otherwise(pl.col("p1_post_rating") - pl.col("p1_pre_rating"))
            .alias("p2_rating_delta"),
            pl.when(
                pl.col("games_won_p1").is_not_null()
                & pl.col("games_won_p2").is_not_null()
            )
            .then(
                pl.when(pl.col("_p1_is_left"))
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
        .with_columns(
            pl.when(pl.col("_p1_score") == 1.0)
            .then(pl.lit(p1_name))
            .when(pl.col("_p1_score") == 0.0)
            .then(pl.lit(p2_name))
            .otherwise(pl.lit("Draw"))
            .alias("winner"),
        )
        .sort(["rating_date", "match_sk"], descending=[True, True], nulls_last=True)
    )

    p1_wins = int(normalized.filter(pl.col("_p1_score") == 1.0).height)
    p2_wins = int(normalized.filter(pl.col("_p1_score") == 0.0).height)
    draws = int(normalized.filter(pl.col("_p1_score") == 0.5).height)

    matches_df = normalized.select(
        pl.col("match_sk").cast(pl.Int64),
        pl.col("rating_date").cast(pl.Date),
        "event_name",
        "round_code",
        "winner",
        "score",
        pl.col("p1_pre_rating").cast(pl.Float64),
        pl.col("p2_pre_rating").cast(pl.Float64),
        pl.col("p1_rating_delta").cast(pl.Float64),
        pl.col("p2_rating_delta").cast(pl.Float64),
    )

    return HeadToHeadSummary(
        p1_player_sk=p1_player_sk,
        p2_player_sk=p2_player_sk,
        p1_name=p1_name,
        p2_name=p2_name,
        p1_rating=p1_rating,
        p1_rd=p1_rd,
        p2_rating=p2_rating,
        p2_rd=p2_rd,
        rating_diff=rating_diff,
        p1_win_prob=p1_win_prob,
        p2_win_prob=p2_win_prob,
        total_matches=matches_df.height,
        p1_wins=p1_wins,
        p2_wins=p2_wins,
        draws=draws,
        matches_df=matches_df,
    )

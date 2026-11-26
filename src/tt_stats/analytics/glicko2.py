"""Glicko-2 match-level rating engine for senior singles matches.

Reads rating-eligible matches from ``marts.fct_senior_singles_matches`` in
chronological order, computes pre- and post-match Glicko-2 ratings (rating, RD,
volatility, and pre-match expected win probability), and atomically populates
``analytics.fct_glicko_match_ratings`` and ``analytics.glicko_run_metadata``.

Run via:
    make glicko-run
or:
    uv run tt-glicko-build
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from typing import Literal, cast

import psycopg
from psycopg.rows import dict_row

from tt_stats.core.config import Settings, get_settings
from tt_stats.core.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

GLICKO2_SCALE = 173.7178
CONVERGENCE_TOL = 1e-6
MAX_ILLINOIS_ITERS = 100

UpdateMode = Literal["match", "event", "week"]

FETCH_MATCHES_SQL = """
select
    m.match_sk,
    m.event_sk,
    m.gender_category,
    coalesce(m.start_date_utc::date, e.start_date) as rating_date,
    m.p1_player_sk,
    m.p2_player_sk,
    m.winner_side
from marts.fct_senior_singles_matches as m
left join marts.dim_events as e
    on m.event_sk = e.event_sk
where
    m.is_rating_eligible
    and m.has_result
    and m.p1_player_sk is not null
    and m.p2_player_sk is not null
order by
    coalesce(m.start_date_utc, e.start_date::timestamptz) asc nulls last,
    m.event_sk asc,
    m.round_order asc nulls last,
    m.match_sk asc
"""

COPY_MATCH_RATINGS_SQL = """
copy analytics.fct_glicko_match_ratings (
    match_sk,
    event_sk,
    gender_category,
    rating_date,
    p1_player_sk,
    p2_player_sk,
    p1_pre_rating,
    p1_pre_rd,
    p1_pre_vol,
    p1_post_rating,
    p1_post_rd,
    p1_post_vol,
    p2_pre_rating,
    p2_pre_rd,
    p2_pre_vol,
    p2_post_rating,
    p2_post_rd,
    p2_post_vol,
    p1_expected_win_prob,
    p1_actual_score
) from stdin
"""

INSERT_METADATA_SQL = """
insert into analytics.glicko_run_metadata (
    matches_rated,
    players_rated,
    config
)
values (%s, %s, %s::jsonb)
"""


@dataclass(frozen=True, slots=True)
class Glicko2Config:
    """Configurable parameters for the Glicko-2 rating engine."""

    initial_rating: float = 1500.0
    initial_rd: float = 350.0
    initial_vol: float = 0.06
    tau: float = 0.5
    rd_decay_days: float | None = None
    update_mode: UpdateMode = "match"
    separate_gender_pools: bool = True

    def __post_init__(self) -> None:
        if self.initial_rd <= 0:
            raise ValueError("initial_rd must be positive")
        if self.initial_vol <= 0:
            raise ValueError("initial_vol must be positive")
        if self.tau <= 0:
            raise ValueError("tau must be positive")
        if self.rd_decay_days is not None and self.rd_decay_days <= 0:
            raise ValueError("rd_decay_days must be positive or None")

    @classmethod
    def from_settings(cls, settings: Settings) -> Glicko2Config:
        """Build a Glicko2Config from application Settings."""
        return cls(
            initial_rating=settings.glicko_initial_rating,
            initial_rd=settings.glicko_initial_rd,
            initial_vol=settings.glicko_initial_vol,
            tau=settings.glicko_tau,
            rd_decay_days=settings.glicko_rd_decay_days,
            update_mode=settings.glicko_update_mode,
            separate_gender_pools=settings.glicko_separate_gender_pools,
        )


@dataclass(frozen=True, slots=True)
class PlayerState:
    """Current Glicko-2 state for one player in rating-scale units."""

    rating: float
    rd: float
    vol: float
    last_date: date | None = None


@dataclass(frozen=True, slots=True)
class MatchInput:
    """Minimal match record needed to replay Glicko-2 ratings."""

    match_sk: int
    event_sk: int
    gender_category: str
    rating_date: date | None
    p1_player_sk: int
    p2_player_sk: int
    winner_side: int | None


@dataclass(frozen=True, slots=True)
class MatchRatingRow:
    """Computed pre- and post-match Glicko-2 ratings for one match."""

    match_sk: int
    event_sk: int
    gender_category: str
    rating_date: date | None
    p1_player_sk: int
    p2_player_sk: int
    p1_pre_rating: float
    p1_pre_rd: float
    p1_pre_vol: float
    p1_post_rating: float
    p1_post_rd: float
    p1_post_vol: float
    p2_pre_rating: float
    p2_pre_rd: float
    p2_pre_vol: float
    p2_post_rating: float
    p2_post_rd: float
    p2_post_vol: float
    p1_expected_win_prob: float
    p1_actual_score: float

    def as_tuple(self) -> tuple[object, ...]:
        """Return fields in COPY column order."""
        return (
            self.match_sk,
            self.event_sk,
            self.gender_category,
            self.rating_date,
            self.p1_player_sk,
            self.p2_player_sk,
            self.p1_pre_rating,
            self.p1_pre_rd,
            self.p1_pre_vol,
            self.p1_post_rating,
            self.p1_post_rd,
            self.p1_post_vol,
            self.p2_pre_rating,
            self.p2_pre_rd,
            self.p2_pre_vol,
            self.p2_post_rating,
            self.p2_post_rd,
            self.p2_post_vol,
            self.p1_expected_win_prob,
            self.p1_actual_score,
        )


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))


def _expected_score(mu: float, opp_mu: float, opp_phi: float) -> float:
    exponent = -_g(opp_phi) * (mu - opp_mu)
    if exponent > 700.0:
        return 0.0
    if exponent < -700.0:
        return 1.0
    return 1.0 / (1.0 + math.exp(exponent))


def expected_win_probability(
    p1: PlayerState,
    p2: PlayerState,
    *,
    base_rating: float = 1500.0,
) -> float:
    """Return pre-match win probability for ``p1`` accounting for both players' RD."""
    mu1 = (p1.rating - base_rating) / GLICKO2_SCALE
    mu2 = (p2.rating - base_rating) / GLICKO2_SCALE
    phi_combined = math.hypot(p1.rd / GLICKO2_SCALE, p2.rd / GLICKO2_SCALE)
    return _expected_score(mu1, mu2, phi_combined)


def apply_inactivity_decay(
    state: PlayerState,
    current_date: date | None,
    config: Glicko2Config,
) -> PlayerState:
    """Return ``state`` with RD decayed for inactivity, or unchanged if decay is off."""
    if (
        config.rd_decay_days is None
        or current_date is None
        or state.last_date is None
        or current_date <= state.last_date
    ):
        return state

    periods = (current_date - state.last_date).days / config.rd_decay_days
    phi = state.rd / GLICKO2_SCALE
    max_phi = config.initial_rd / GLICKO2_SCALE
    new_phi = min(math.hypot(phi, math.sqrt(periods) * state.vol), max_phi)
    return PlayerState(
        rating=state.rating,
        rd=new_phi * GLICKO2_SCALE,
        vol=state.vol,
        last_date=state.last_date,
    )


def update_player_state(
    player: PlayerState,
    results: Sequence[tuple[PlayerState, float]],
    config: Glicko2Config,
    *,
    new_date: date | None = None,
) -> PlayerState:
    """Compute one Glicko-2 rating-period update for a single player.

    Args:
        player: Player's pre-period state (already decayed if applicable).
        results: Sequence of ``(opponent_pre_state, score)`` where ``score`` is
            ``1.0`` (win), ``0.5`` (draw), or ``0.0`` (loss).
        config: Glicko-2 parameters.
        new_date: Date to record as ``last_date`` on the returned state.
    """
    if not results:
        return player

    base = config.initial_rating
    mu = (player.rating - base) / GLICKO2_SCALE
    phi = player.rd / GLICKO2_SCALE
    sigma = player.vol
    tau = config.tau

    v_inv = 0.0
    delta_sum = 0.0
    for opp, score in results:
        opp_mu = (opp.rating - base) / GLICKO2_SCALE
        opp_phi = opp.rd / GLICKO2_SCALE
        g_j = _g(opp_phi)
        e_j = _expected_score(mu, opp_mu, opp_phi)
        v_inv += g_j * g_j * e_j * (1.0 - e_j)
        delta_sum += g_j * (score - e_j)

    if v_inv <= 0.0:
        return PlayerState(
            rating=player.rating,
            rd=player.rd,
            vol=player.vol,
            last_date=new_date or player.last_date,
        )

    v = 1.0 / v_inv
    delta = v * delta_sum
    phi_sq = phi * phi
    delta_sq = delta * delta
    tau_sq = tau * tau
    a = math.log(sigma * sigma)

    def _f(x: float) -> float:
        ex = math.exp(x)
        denom = phi_sq + v + ex
        return (ex * (delta_sq - phi_sq - v - ex)) / (2.0 * denom * denom) - (
            (x - a) / tau_sq
        )

    low = a
    if delta_sq > phi_sq + v:
        high = math.log(delta_sq - phi_sq - v)
    else:
        k = 1
        while _f(a - k * tau) < 0.0:
            k += 1
        high = a - k * tau

    f_low = _f(low)
    f_high = _f(high)

    for _ in range(MAX_ILLINOIS_ITERS):
        if abs(high - low) <= CONVERGENCE_TOL:
            break
        mid = low + (low - high) * f_low / (f_high - f_low)
        f_mid = _f(mid)
        if f_mid * f_high <= 0.0:
            low = high
            f_low = f_high
        else:
            f_low /= 2.0
        high = mid
        f_high = f_mid

    new_sigma = math.exp(low / 2.0)
    phi_star = math.hypot(phi, new_sigma)
    new_phi = 1.0 / math.sqrt((1.0 / (phi_star * phi_star)) + (1.0 / v))
    new_mu = mu + (new_phi * new_phi) * delta_sum

    return PlayerState(
        rating=base + GLICKO2_SCALE * new_mu,
        rd=GLICKO2_SCALE * new_phi,
        vol=new_sigma,
        last_date=new_date or player.last_date,
    )


def _match_score_for_p1(winner_side: int | None) -> float:
    if winner_side == 1:
        return 1.0
    if winner_side == 2:
        return 0.0
    return 0.5


def _period_key(match: MatchInput, mode: UpdateMode) -> tuple[object, ...]:
    if mode == "match":
        return ("match", match.match_sk)
    if mode == "event":
        return ("event", match.event_sk)
    if match.rating_date is not None:
        iso_year, iso_week, _ = match.rating_date.isocalendar()
        return ("week", iso_year, iso_week)
    return ("week_event_fallback", match.event_sk)


def compute_glicko2_ratings(
    matches: Sequence[MatchInput],
    config: Glicko2Config,
) -> tuple[list[MatchRatingRow], int]:
    """Replay matches chronologically and return per-match rating rows.

    Returns ``(rows, distinct_players_rated)``.
    """
    if not matches:
        return [], 0

    default_state = PlayerState(
        rating=config.initial_rating,
        rd=config.initial_rd,
        vol=config.initial_vol,
        last_date=None,
    )

    # Keyed by (pool_name, player_sk) so men's and women's pools stay isolated.
    states: dict[tuple[str, int], PlayerState] = {}
    rated_players: set[int] = set()
    output: list[MatchRatingRow] = []

    def _pool_for(match: MatchInput) -> str:
        return match.gender_category if config.separate_gender_pools else "all"

    idx = 0
    total = len(matches)
    while idx < total:
        first = matches[idx]
        pool = _pool_for(first)
        batch_key = (pool, _period_key(first, config.update_mode))
        batch: list[MatchInput] = []
        while idx < total:
            candidate = matches[idx]
            cand_key = (
                _pool_for(candidate),
                _period_key(candidate, config.update_mode),
            )
            if cand_key != batch_key:
                break
            batch.append(candidate)
            idx += 1

        # Snapshot pre-period states (applying optional inactivity decay once).
        pre_states: dict[int, PlayerState] = {}
        period_results: dict[int, list[tuple[PlayerState, float]]] = defaultdict(list)
        latest_date_in_batch: dict[int, date | None] = {}

        for m in batch:
            for pid in (m.p1_player_sk, m.p2_player_sk):
                if pid not in pre_states:
                    raw = states.get((pool, pid), default_state)
                    pre_states[pid] = apply_inactivity_decay(raw, m.rating_date, config)
                if m.rating_date is not None:
                    prev_d = latest_date_in_batch.get(pid)
                    if prev_d is None or m.rating_date > prev_d:
                        latest_date_in_batch[pid] = m.rating_date

        for m in batch:
            p1_score = _match_score_for_p1(m.winner_side)
            p2_score = 1.0 - p1_score
            p1_pre = pre_states[m.p1_player_sk]
            p2_pre = pre_states[m.p2_player_sk]
            period_results[m.p1_player_sk].append((p2_pre, p1_score))
            period_results[m.p2_player_sk].append((p1_pre, p2_score))

        # Compute post-period state for every player active in this batch.
        post_states: dict[int, PlayerState] = {}
        for pid, opp_results in period_results.items():
            post_state = update_player_state(
                pre_states[pid],
                opp_results,
                config,
                new_date=latest_date_in_batch.get(pid),
            )
            post_states[pid] = post_state
            states[(pool, pid)] = post_state
            rated_players.add(pid)

        for m in batch:
            p1_pre = pre_states[m.p1_player_sk]
            p2_pre = pre_states[m.p2_player_sk]
            p1_post = post_states[m.p1_player_sk]
            p2_post = post_states[m.p2_player_sk]
            p1_score = _match_score_for_p1(m.winner_side)
            win_prob = expected_win_probability(
                p1_pre,
                p2_pre,
                base_rating=config.initial_rating,
            )
            output.append(
                MatchRatingRow(
                    match_sk=m.match_sk,
                    event_sk=m.event_sk,
                    gender_category=m.gender_category,
                    rating_date=m.rating_date,
                    p1_player_sk=m.p1_player_sk,
                    p2_player_sk=m.p2_player_sk,
                    p1_pre_rating=p1_pre.rating,
                    p1_pre_rd=p1_pre.rd,
                    p1_pre_vol=p1_pre.vol,
                    p1_post_rating=p1_post.rating,
                    p1_post_rd=p1_post.rd,
                    p1_post_vol=p1_post.vol,
                    p2_pre_rating=p2_pre.rating,
                    p2_pre_rd=p2_pre.rd,
                    p2_pre_vol=p2_pre.vol,
                    p2_post_rating=p2_post.rating,
                    p2_post_rd=p2_post.rd,
                    p2_post_vol=p2_post.vol,
                    p1_expected_win_prob=win_prob,
                    p1_actual_score=p1_score,
                )
            )

    return output, len(rated_players)


def fetch_rating_eligible_matches(
    conn: psycopg.Connection[dict[str, object]],
) -> list[MatchInput]:
    """Load rating-eligible senior singles matches in chronological order."""
    rows = conn.execute(FETCH_MATCHES_SQL).fetchall()
    result: list[MatchInput] = []
    for row in rows:
        winner_raw = row["winner_side"]
        rating_date_raw = row["rating_date"]
        result.append(
            MatchInput(
                match_sk=int(cast(int, row["match_sk"])),
                event_sk=int(cast(int, row["event_sk"])),
                gender_category=str(row["gender_category"]),
                rating_date=(
                    rating_date_raw if isinstance(rating_date_raw, date) else None
                ),
                p1_player_sk=int(cast(int, row["p1_player_sk"])),
                p2_player_sk=int(cast(int, row["p2_player_sk"])),
                winner_side=(int(winner_raw) if isinstance(winner_raw, int) else None),
            )
        )
    return result


def write_glicko2_ratings(
    conn: psycopg.Connection[dict[str, object]],
    rows: Sequence[MatchRatingRow],
    players_rated: int,
    config: Glicko2Config,
) -> None:
    """Replace ``analytics.fct_glicko_match_ratings`` atomically and log metadata."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("truncate table analytics.fct_glicko_match_ratings;")
        with cur.copy(COPY_MATCH_RATINGS_SQL) as copy:
            for item in rows:
                copy.write_row(item.as_tuple())
        cur.execute(
            INSERT_METADATA_SQL,
            (len(rows), players_rated, json.dumps(asdict(config))),
        )


def build_and_store_ratings(
    settings: Settings,
    config: Glicko2Config | None = None,
    *,
    dry_run: bool = False,
) -> tuple[int, int, float]:
    """Fetch matches, compute Glicko-2 ratings, and persist to Postgres.

    Returns ``(matches_rated, players_rated, elapsed_seconds)``.
    """
    active_config = config or Glicko2Config.from_settings(settings)
    started = time.perf_counter()

    with psycopg.connect(settings.dsn, row_factory=dict_row) as conn:
        matches = fetch_rating_eligible_matches(conn)
        if not matches:
            raise RuntimeError(
                "No rating-eligible matches found in marts.fct_senior_singles_matches"
            )
        rated_rows, players_rated = compute_glicko2_ratings(matches, active_config)
        if not dry_run:
            write_glicko2_ratings(conn, rated_rows, players_rated, active_config)

    elapsed = time.perf_counter() - started
    logger.info(
        "glicko2_build_complete",
        extra={
            "matches_rated": len(rated_rows),
            "players_rated": players_rated,
            "elapsed_seconds": round(elapsed, 3),
            "dry_run": dry_run,
            "config": asdict(active_config),
        },
    )
    return len(rated_rows), players_rated, elapsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tt-glicko-build",
        description="Recompute Glicko-2 match ratings and load into Postgres.",
    )
    parser.add_argument("--initial-rating", type=float, default=None)
    parser.add_argument("--initial-rd", type=float, default=None)
    parser.add_argument("--initial-vol", type=float, default=None)
    parser.add_argument("--tau", type=float, default=None)
    parser.add_argument(
        "--rd-decay-days",
        type=float,
        default=None,
        help="Days per inactivity decay period (omit or <=0 for zero decay).",
    )
    parser.add_argument(
        "--update-mode",
        choices=("match", "event", "week"),
        default=None,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute ratings without writing to Postgres.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for ``tt-glicko-build``."""
    configure_logging()
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    base_cfg = Glicko2Config.from_settings(settings)

    rd_decay_days = base_cfg.rd_decay_days
    if args.rd_decay_days is not None:
        rd_decay_days = args.rd_decay_days if args.rd_decay_days > 0 else None

    config = Glicko2Config(
        initial_rating=(
            args.initial_rating
            if args.initial_rating is not None
            else base_cfg.initial_rating
        ),
        initial_rd=(
            args.initial_rd if args.initial_rd is not None else base_cfg.initial_rd
        ),
        initial_vol=(
            args.initial_vol if args.initial_vol is not None else base_cfg.initial_vol
        ),
        tau=args.tau if args.tau is not None else base_cfg.tau,
        rd_decay_days=rd_decay_days,
        update_mode=(
            args.update_mode if args.update_mode is not None else base_cfg.update_mode
        ),
        separate_gender_pools=base_cfg.separate_gender_pools,
    )

    matches_rated, players_rated, elapsed = build_and_store_ratings(
        settings,
        config,
        dry_run=args.dry_run,
    )
    mode_label = "dry-run" if args.dry_run else "saved"
    decay_label = (
        f"{config.rd_decay_days:g}d" if config.rd_decay_days is not None else "off"
    )
    print(  # noqa: T201
        f"Glicko-2 ({mode_label}, mode={config.update_mode}, decay={decay_label}): "
        f"{matches_rated:,} matches, {players_rated:,} players in {elapsed:.2f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

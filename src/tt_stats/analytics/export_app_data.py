"""Export warehouse marts to compact Parquet snapshots for the Streamlit app.

Run via:
    make app-refresh
or:
    uv run tt-app-export

The exported Parquet files are written to ``app/data/`` (explicitly tracked in
git via ``!app/data/*.parquet`` while other ``*.parquet`` files stay ignored)
so Streamlit Community Cloud can load everything without hitting Postgres.
``dim_players.parquet`` includes only senior-singles players and exports
integer ``age`` at export time rather than ``dob``.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl
import psycopg
from psycopg.rows import dict_row

from tt_stats.core.config import Settings, project_root

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExportQuery:
    """A named SQL query and its destination Parquet filename."""

    filename: str
    sql: str


# Each entry defines one Parquet snapshot written to app/data/.
# Keep queries static (no string interpolation) so sqlfluff and bandit stay happy.
EXPORT_QUERIES: tuple[ExportQuery, ...] = (
    ExportQuery(
        filename="dim_events.parquet",
        sql="""
        select
            event_sk,
            source_event_id,
            event_name,
            event_type,
            event_tier_name,
            event_year,
            start_date,
            end_date,
            duration_days,
            country,
            city,
            continent_code,
            ball_sponsor,
            table_sponsor,
            has_matches,
            has_match_details,
            has_commentary,
            has_sponsors
        from marts.dim_events
        """,
    ),
    ExportQuery(
        filename="dim_players.parquet",
        sql="""
        with senior_players as (
            select p1_player_sk as player_sk
            from marts.fct_senior_singles_matches
            where p1_player_sk is not null
            union
            select p2_player_sk as player_sk
            from marts.fct_senior_singles_matches
            where p2_player_sk is not null
        )
        select
            p.player_sk,
            p.ittf_id,
            p.player_name,
            p.country_code,
            p.country_name,
            p.organization_code,
            p.organization_name,
            case
                when
                    p.dob is not null
                    and p.dob >= (current_date - interval '120 years')::date
                    and p.dob <= current_date
                    then extract(year from age(current_date, p.dob))::integer
            end as age,
            p.gender,
            p.hand,
            p.grip,
            p.style,
            p.head_shot,
            p.has_wtt_profile
        from marts.dim_players as p
        inner join senior_players as sp
            on p.player_sk = sp.player_sk
        """,
    ),
    ExportQuery(
        filename="fct_senior_singles_matches.parquet",
        sql="""
        select
            match_sk,
            event_sk,
            p1_player_sk,
            p2_player_sk,
            winner_player_sk,
            loser_player_sk,
            winner_side,
            gender_category,
            round_code,
            round_stage,
            round_order,
            start_date_utc,
            start_date_local,
            match_format,
            best_of,
            scheduled_games,
            result_type,
            is_default_result,
            is_rating_eligible,
            has_result,
            has_score_anomaly,
            games_won_p1,
            games_won_p2,
            points_won_p1,
            points_won_p2
        from marts.fct_senior_singles_matches
        """,
    ),
    ExportQuery(
        filename="fct_senior_singles_games.parquet",
        sql="""
        select
            match_sk,
            game_number,
            p1_points,
            p2_points,
            point_margin,
            winner_side,
            is_valid_game_score
        from marts.fct_senior_singles_games
        """,
    ),
    ExportQuery(
        filename="catalog_matches_by_event_gender.parquet",
        sql="""
        select
            event_sk,
            gender_category,
            discipline,
            is_youth,
            is_para,
            count(*)::bigint as match_count
        from marts.dim_matches
        group by 1, 2, 3, 4, 5
        """,
    ),
    ExportQuery(
        filename="fct_glicko_match_ratings.parquet",
        sql="""
        select
            match_sk,
            event_sk,
            gender_category,
            rating_date,
            p1_player_sk,
            p2_player_sk,
            round(p1_pre_rating::numeric, 2)::real as p1_pre_rating,
            round(p1_pre_rd::numeric, 2)::real as p1_pre_rd,
            round(p1_pre_vol::numeric, 5)::real as p1_pre_vol,
            round(p1_post_rating::numeric, 2)::real as p1_post_rating,
            round(p1_post_rd::numeric, 2)::real as p1_post_rd,
            round(p1_post_vol::numeric, 5)::real as p1_post_vol,
            round(p2_pre_rating::numeric, 2)::real as p2_pre_rating,
            round(p2_pre_rd::numeric, 2)::real as p2_pre_rd,
            round(p2_pre_vol::numeric, 5)::real as p2_pre_vol,
            round(p2_post_rating::numeric, 2)::real as p2_post_rating,
            round(p2_post_rd::numeric, 2)::real as p2_post_rd,
            round(p2_post_vol::numeric, 5)::real as p2_post_vol,
            round(p1_expected_win_prob::numeric, 4)::real as p1_expected_win_prob,
            p1_actual_score::real as p1_actual_score
        from analytics.fct_glicko_match_ratings
        """,
    ),
)


LAST_COLLECTED_SQL = """
select max(finished_at) as last_collected_at
from ingest_wtt.ingest_runs
where status = 'succeeded'
"""

MARTS_BUILT_SQL = """
select built_at_utc
from marts.mart_metadata
limit 1
"""

SERVE_RECEIVE_FILENAME = "player_serve_receive_stats.parquet"

COMMENTARY_EVENTS_SQL = """
with match_players as (
    select
        m.match_sk,
        m.p1_player_sk,
        m.p2_player_sk,
        upper(trim(p1.player_family_name || ' ' || p1.player_given_name)) as p1_fg,
        upper(trim(p2.player_family_name || ' ' || p2.player_given_name)) as p2_fg
    from marts.fct_senior_singles_matches as m
    inner join marts.dim_players as p1
        on m.p1_player_sk = p1.player_sk
    inner join marts.dim_players as p2
        on m.p2_player_sk = p2.player_sk
    where
        m.has_result
        and not m.is_default_result
        and m.result_type = 'played'
        and m.p1_player_sk is not null
        and m.p2_player_sk is not null
),

events as (
    select
        c.match_sk,
        c.sequence_number,
        c.action_type,
        mp.p1_player_sk,
        mp.p2_player_sk,
        mp.p1_fg,
        mp.p2_fg,
        case
            when c.action_type = 'PT' then
                upper(trim(substring(c.commentary_text from '^Point won by ([^,]+),')))
            when c.action_type = 'SSR' then
                upper(
                    trim(
                        substring(
                            c.commentary_text from '^Service change: (.+) to serve$'
                        )
                    )
                )
        end as actor_name
    from marts.fct_match_commentary as c
    inner join match_players as mp
        on c.match_sk = mp.match_sk
    where c.action_type in ('PT', 'SSR', 'DPT')
),

tagged as (
    select
        match_sk,
        sequence_number,
        action_type,
        p1_player_sk,
        p2_player_sk,
        case
            when actor_name is null then null
            when actor_name = 'UNKNOWN PLAYER' then 0
            when actor_name = p1_fg then 1
            when actor_name = p2_fg then 2
            else -1
        end as raw_side
    from events
),

match_flags as (
    select
        match_sk,
        bool_or(raw_side = 1) as has_p1,
        bool_or(raw_side = 2) as has_p2,
        bool_or(raw_side is null and action_type = 'PT') as has_null_pt
    from tagged
    group by match_sk
)

select
    t.match_sk,
    t.sequence_number,
    t.action_type,
    t.p1_player_sk,
    t.p2_player_sk,
    case
        when t.raw_side in (0, 1, 2) then t.raw_side
        when t.raw_side = -1 and mf.has_p1 and not mf.has_p2 then 2
        when t.raw_side = -1 and mf.has_p2 and not mf.has_p1 then 1
        else t.raw_side
    end as actor_side
from tagged as t
inner join match_flags as mf
    on t.match_sk = mf.match_sk
where not mf.has_null_pt
order by t.match_sk, t.sequence_number
"""

COMMENTARY_EVENT_SCHEMA: dict[str, pl.DataType] = {
    "match_sk": pl.Int64(),
    "sequence_number": pl.Int32(),
    "action_type": pl.String(),
    "p1_player_sk": pl.Int64(),
    "p2_player_sk": pl.Int64(),
    "actor_side": pl.Int8(),
}


def resolve_commentary_points(events: pl.DataFrame) -> pl.DataFrame:
    """Resolve server/receiver per point and cancel DPT-deleted points per match.

    Walks ``PT``, ``SSR``, and ``DPT`` commentary rows ordered by
    ``(match_sk, sequence_number)``, infers the opening server before the first
    ``SSR`` (accounting for ``UNKNOWN PLAYER`` toggles), flips service on
    subsequent ``SSR`` events, and pops deleted points on ``DPT``.
    """
    if events.is_empty():
        return pl.DataFrame(
            schema={
                "match_sk": pl.Int64(),
                "p1_player_sk": pl.Int64(),
                "p2_player_sk": pl.Int64(),
                "actor_side": pl.Int8(),
                "server_side": pl.Int8(),
            }
        )

    ordered = events.sort(["match_sk", "sequence_number"])
    m_arr = ordered["match_sk"].to_numpy()
    at_arr = ordered["action_type"].to_numpy()
    side_arr = ordered["actor_side"].fill_null(-99).to_numpy()

    n = len(m_arr)
    keep_pt = np.zeros(n, dtype=bool)
    server_arr = np.zeros(n, dtype=np.int8)

    i = 0
    while i < n:
        j = i
        ms = m_arr[i]
        while j < n and m_arr[j] == ms:
            j += 1

        # Infer opening server before first SSR (many matches log PT before first SSR).
        cur_server = 0
        unknown_ssr_before = 0
        for k in range(i, j):
            if at_arr[k] == "SSR":
                s_val = int(side_arr[k])
                if s_val in (1, 2):
                    cur_server = (3 - s_val) if (unknown_ssr_before % 2 == 0) else s_val
                    break
                unknown_ssr_before += 1

        if cur_server != 0:
            pt_stack: list[int] = []
            for k in range(i, j):
                act = at_arr[k]
                if act == "SSR":
                    s_val = int(side_arr[k])
                    cur_server = s_val if s_val in (1, 2) else (3 - cur_server)
                elif act == "PT":
                    s_val = int(side_arr[k])
                    if s_val in (1, 2):
                        server_arr[k] = cur_server
                        keep_pt[k] = True
                        pt_stack.append(k)
                elif act == "DPT" and pt_stack:
                    keep_pt[pt_stack.pop()] = False
        i = j

    return (
        ordered.with_columns(
            pl.Series("server_side", server_arr),
            pl.Series("keep_pt", keep_pt),
        )
        .filter(pl.col("keep_pt"))
        .select("match_sk", "p1_player_sk", "p2_player_sk", "actor_side", "server_side")
    )


def _clean_profile_attr(col_name: str, alias: str) -> pl.Expr:
    cleaned = pl.col(col_name).cast(pl.String).str.strip_chars()
    return (
        pl.when(cleaned.is_null() | (cleaned == ""))
        .then(pl.lit("Unknown"))
        .otherwise(cleaned)
        .alias(alias)
    )


def build_player_serve_receive_stats(
    events: pl.DataFrame,
    players: pl.DataFrame,
) -> pl.DataFrame:
    """Aggregate commentary-level point outcomes into per-player serve/receive stats."""
    pts = resolve_commentary_points(events)
    if pts.is_empty():
        return pl.DataFrame()

    p1_pts = pts.select(
        pl.col("match_sk"),
        pl.col("p1_player_sk").alias("player_sk"),
        (pl.col("actor_side") == 1).cast(pl.UInt32).alias("won"),
        (pl.col("server_side") == 1).cast(pl.UInt32).alias("served"),
        ((pl.col("server_side") == 1) & (pl.col("actor_side") == 1))
        .cast(pl.UInt32)
        .alias("serve_won"),
        (pl.col("server_side") == 2).cast(pl.UInt32).alias("received"),
        ((pl.col("server_side") == 2) & (pl.col("actor_side") == 1))
        .cast(pl.UInt32)
        .alias("receive_won"),
    )
    p2_pts = pts.select(
        pl.col("match_sk"),
        pl.col("p2_player_sk").alias("player_sk"),
        (pl.col("actor_side") == 2).cast(pl.UInt32).alias("won"),
        (pl.col("server_side") == 2).cast(pl.UInt32).alias("served"),
        ((pl.col("server_side") == 2) & (pl.col("actor_side") == 2))
        .cast(pl.UInt32)
        .alias("serve_won"),
        (pl.col("server_side") == 1).cast(pl.UInt32).alias("received"),
        ((pl.col("server_side") == 1) & (pl.col("actor_side") == 2))
        .cast(pl.UInt32)
        .alias("receive_won"),
    )

    norm_players = players.select(
        pl.col("player_sk").cast(pl.Int64),
        pl.col("player_name").fill_null("Unknown Player").alias("Player"),
        pl.col("organization_code").fill_null("").alias("Organization"),
        pl.when(pl.col("gender") == "M")
        .then(pl.lit("Men"))
        .when(pl.col("gender") == "F")
        .then(pl.lit("Women"))
        .otherwise(pl.lit("Unknown"))
        .alias("Gender"),
        _clean_profile_attr("style", "Style"),
        _clean_profile_attr("grip", "Grip"),
        _clean_profile_attr("hand", "Hand"),
    )

    return (
        pl.concat([p1_pts, p2_pts])
        .group_by("player_sk")
        .agg(
            pl.col("match_sk").n_unique().cast(pl.Int64).alias("Matches"),
            pl.len().cast(pl.Int64).alias("Points Played"),
            pl.col("won").sum().cast(pl.Int64).alias("Points Won"),
            pl.col("served").sum().cast(pl.Int64).alias("Serve Points"),
            pl.col("serve_won").sum().cast(pl.Int64).alias("Serve Won"),
            pl.col("received").sum().cast(pl.Int64).alias("Receive Points"),
            pl.col("receive_won").sum().cast(pl.Int64).alias("Receive Won"),
        )
        .with_columns(
            (pl.col("Points Played") - pl.col("Points Won")).alias("Points Lost"),
            (pl.col("Serve Points") - pl.col("Serve Won")).alias("Serve Lost"),
            (pl.col("Receive Points") - pl.col("Receive Won")).alias("Receive Lost"),
        )
        .join(norm_players, on="player_sk", how="inner")
        .select(
            "player_sk",
            "Player",
            "Organization",
            "Gender",
            "Style",
            "Grip",
            "Hand",
            "Matches",
            "Points Played",
            "Points Won",
            "Points Lost",
            "Serve Points",
            "Serve Won",
            "Serve Lost",
            "Receive Points",
            "Receive Won",
            "Receive Lost",
        )
        .sort(["Matches", "Points Played"], descending=True)
    )


def _format_utc(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    return ts.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _fetch_marts_built_at(
    conn: psycopg.Connection[dict[str, object]],
) -> datetime | None:
    has_table_row = conn.execute(
        "select to_regclass('marts.mart_metadata') is not null as exists"
    ).fetchone()
    if not has_table_row or not has_table_row["exists"]:
        return None
    row = conn.execute(MARTS_BUILT_SQL).fetchone()
    value = row["built_at_utc"] if row else None
    return value if isinstance(value, datetime) else None


def default_output_dir() -> Path:
    """Return the default ``app/data`` directory at the repository root."""
    return project_root() / "app" / "data"


def export_all(settings: Settings, output_dir: Path | None = None) -> dict[str, int]:
    """Run every export query against Postgres and write Parquet files atomically.

    All Parquet files and ``snapshot_metadata.json`` are written into a sibling
    temporary directory first and moved into ``output_dir`` only after every
    query produces a non-empty DataFrame. Any failure or empty result leaves
    pre-existing files in ``output_dir`` untouched.

    Returns a mapping of ``filename -> row_count`` for logging and tests.
    """
    target_dir = output_dir or default_output_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}
    with tempfile.TemporaryDirectory(
        prefix=f".{target_dir.name}.tmp-",
        dir=target_dir.parent,
    ) as tmp_dir_name:
        staging_dir = Path(tmp_dir_name)
        staged_files: list[Path] = []

        with psycopg.connect(settings.dsn, row_factory=dict_row) as conn:
            exported_frames: dict[str, pl.DataFrame] = {}
            for item in EXPORT_QUERIES:
                rows = conn.execute(item.sql).fetchall()
                if not rows:
                    raise RuntimeError(
                        f"Export query for {item.filename} returned 0 rows; "
                        "aborting export"
                    )
                df = pl.DataFrame(rows, infer_schema_length=None)
                if df.is_empty() or df.width == 0:
                    raise RuntimeError(
                        f"Export query for {item.filename} produced an empty frame; "
                        "aborting export"
                    )
                exported_frames[item.filename] = df
                staged_path = staging_dir / item.filename
                df.write_parquet(staged_path, compression="zstd")
                staged_files.append(staged_path)
                counts[item.filename] = df.height
                size_kb = staged_path.stat().st_size / 1024
                logger.info(
                    "Exported %s (%d rows, %.1f KB)",
                    item.filename,
                    df.height,
                    size_kb,
                )

            commentary_rows = conn.execute(COMMENTARY_EVENTS_SQL).fetchall()
            if not commentary_rows:
                raise RuntimeError(
                    f"Export query for {SERVE_RECEIVE_FILENAME} returned 0 rows; "
                    "aborting export"
                )
            commentary_events = pl.DataFrame(
                commentary_rows,
                schema=COMMENTARY_EVENT_SCHEMA,
            )
            sr_df = build_player_serve_receive_stats(
                commentary_events,
                exported_frames["dim_players.parquet"],
            )
            if sr_df.is_empty() or sr_df.width == 0:
                raise RuntimeError(
                    f"Export aggregation for {SERVE_RECEIVE_FILENAME} produced an "
                    "empty frame; aborting export"
                )
            sr_staged_path = staging_dir / SERVE_RECEIVE_FILENAME
            sr_df.write_parquet(sr_staged_path, compression="zstd")
            staged_files.append(sr_staged_path)
            counts[SERVE_RECEIVE_FILENAME] = sr_df.height
            logger.info(
                "Exported %s (%d rows, %.1f KB)",
                SERVE_RECEIVE_FILENAME,
                sr_df.height,
                sr_staged_path.stat().st_size / 1024,
            )

            marts_built_at = _fetch_marts_built_at(conn)
            last_collected_row = conn.execute(LAST_COLLECTED_SQL).fetchone()

        last_collected_raw = (
            last_collected_row["last_collected_at"] if last_collected_row else None
        )
        last_collected_at = (
            last_collected_raw if isinstance(last_collected_raw, datetime) else None
        )
        metadata = {
            "marts_built_at_utc": _format_utc(marts_built_at),
            "last_collected_at_utc": _format_utc(last_collected_at),
            "exported_at_utc": _format_utc(datetime.now(tz=UTC)),
        }
        staged_meta = staging_dir / "snapshot_metadata.json"
        staged_meta.write_text(
            json.dumps(metadata, indent=2) + "\n",
            encoding="utf-8",
        )
        staged_files.append(staged_meta)

        for staged_path in staged_files:
            staged_path.replace(target_dir / staged_path.name)

    return counts


def main() -> None:
    """CLI entry point for ``tt-app-export``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings()
    export_all(settings)


if __name__ == "__main__":
    main()

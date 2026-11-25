"""Export warehouse marts to compact Parquet snapshots for the Streamlit app.

Run via:
    make app-export
or:
    uv run tt-app-export

The exported Parquet files are written to ``app/data/`` (gitignored via ``*.parquet``)
so the Streamlit app can load everything instantaneously without hitting Postgres.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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
)


LAST_COLLECTED_SQL = """
select max(finished_at) as last_collected_at
from ingest_wtt.ingest_runs
where status = 'succeeded'
"""


def default_output_dir() -> Path:
    """Return the default ``app/data`` directory at the repository root."""
    return project_root() / "app" / "data"


def export_all(settings: Settings, output_dir: Path | None = None) -> dict[str, int]:
    """Run every export query against Postgres and write Parquet files.

    Returns a mapping of ``filename -> row_count`` for logging and tests.
    """
    target_dir = output_dir or default_output_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}
    with psycopg.connect(settings.dsn, row_factory=dict_row) as conn:
        for item in EXPORT_QUERIES:
            rows = conn.execute(item.sql).fetchall()
            df = pl.DataFrame(rows, infer_schema_length=None)
            out_path = target_dir / item.filename
            df.write_parquet(out_path, compression="zstd")
            counts[item.filename] = df.height
            size_kb = out_path.stat().st_size / 1024
            logger.info(
                "Exported %s (%d rows, %.1f KB)",
                item.filename,
                df.height,
                size_kb,
            )

        # Record the last successful ingestion timestamp (and export time)
        last_collected_row = conn.execute(LAST_COLLECTED_SQL).fetchone()
    last_collected_at = (
        last_collected_row["last_collected_at"] if last_collected_row else None
    )
    last_collected_str = (
        last_collected_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        if last_collected_at is not None
        else None
    )
    metadata = {
        "last_collected_at_utc": last_collected_str,
        "exported_at_utc": datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC"),
    }
    (target_dir / "snapshot_metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )

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

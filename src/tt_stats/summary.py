"""Generate a Markdown summary of all data currently stored in PostgreSQL."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg import Connection

from tt_stats.core.config import project_root
from tt_stats.core.db import get_connection
from tt_stats.core.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

DEFAULT_OUTPUT_PATH = project_root() / "docs" / "data-summary.md"


def _fmt_int(value: int | None) -> str:
    """Format an integer with thousands separators, or '-' if None."""
    if value is None:
        return "-"
    return f"{value:,}"


def _fmt_pct(part: int, total: int) -> str:
    """Format part/total as a percentage string."""
    if total <= 0:
        return "0.0%"
    return f"{(part / total) * 100:.1f}%"


def _fmt_pair(has_count: int, total: int) -> tuple[str, str]:
    """Return formatted 'has' and 'does not have' strings with percentages."""
    missing = max(total - has_count, 0)
    return (
        f"{_fmt_int(has_count)} ({_fmt_pct(has_count, total)})",
        f"{_fmt_int(missing)} ({_fmt_pct(missing, total)})",
    )


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Render a GitHub-flavored Markdown table."""
    header_line = "| " + " | ".join(headers) + " |"
    sep_line = "| " + " | ".join("---" for _ in headers) + " |"
    body_lines = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([header_line, sep_line, *body_lines])


def _fetch_one(conn: Connection[Any], query: str) -> dict[str, Any]:
    """Execute a query expected to return one row."""
    row = conn.execute(query).fetchone()
    if row is None:
        return {}
    return dict(row)


def _fetch_all(conn: Connection[Any], query: str) -> list[dict[str, Any]]:
    """Execute a query returning zero or more rows."""
    return [dict(row) for row in conn.execute(query).fetchall()]


def _coverage_row(
    label: str,
    total: int,
    with_data: int,
    *,
    notes: str = "",
) -> list[str]:
    """Build a standard 'Total / With Data / Without Data' table row."""
    has_str, missing_str = _fmt_pair(with_data, total)
    return [label, _fmt_int(total), has_str, missing_str, notes]


def _field_completeness_rows(
    conn: Connection[Any],
    table: str,
    columns: Sequence[tuple[str, str]],
) -> list[list[str]]:
    """Compute non-null, non-blank counts for each column in a table."""
    total_row = _fetch_one(conn, f"SELECT COUNT(*) AS total FROM {table};")
    total = int(total_row.get("total", 0))
    if total == 0:
        return [[label, "0", "0 (0.0%)", "0 (0.0%)"] for _, label in columns]

    exprs = [
        f"COUNT(*) FILTER (WHERE {col} IS NOT NULL AND btrim({col}::text) <> '') "
        f"AS {col}_filled"
        for col, _ in columns
    ]
    counts = _fetch_one(conn, f"SELECT {', '.join(exprs)} FROM {table};")
    rows: list[list[str]] = []
    for col, label in columns:
        filled = int(counts.get(f"{col}_filled", 0))
        has_str, missing_str = _fmt_pair(filled, total)
        rows.append([label, _fmt_int(total), has_str, missing_str])
    return rows


def build_summary_markdown(conn: Connection[Any]) -> str:
    """Query PostgreSQL and build the full Markdown data summary."""
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    db_meta = _fetch_one(
        conn,
        """
        SELECT
            current_database() AS db_name,
            pg_size_pretty(pg_database_size(current_database())) AS db_size,
            (SELECT COUNT(*) FROM ingest.schema_version) AS migrations_applied
        ;
        """,
    )

    table_stats = _fetch_all(
        conn,
        """
        SELECT
            n.nspname AS schema_name,
            c.relname AS table_name,
            pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size
        FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r'
          AND n.nspname IN ('ingest', 'analytics')
        ORDER BY n.nspname, pg_total_relation_size(c.oid) DESC, c.relname;
        """,
    )
    for table_row in table_stats:
        schema_name = str(table_row["schema_name"])
        table_name = str(table_row["table_name"])
        count_row = _fetch_one(
            conn, f'SELECT COUNT(*) AS exact_rows FROM "{schema_name}"."{table_name}";'
        )
        table_row["exact_rows"] = int(count_row.get("exact_rows", 0))

    # Calendar years
    years_stats = _fetch_one(
        conn,
        """
        SELECT
            COUNT(*) AS fetched_years,
            MIN(year) AS min_year,
            MAX(year) AS max_year,
            COUNT(*) FILTER (WHERE received_count > 0) AS years_with_events,
            COUNT(*) FILTER (WHERE received_count = 0) AS empty_years,
            COALESCE(SUM(received_count), 0) AS raw_event_rows,
            (SELECT COUNT(*) FROM ingest.events_years_pending) AS pending_years
        FROM ingest.events_by_year_raw;
        """,
    )

    # Events / Tournaments
    events_stats = _fetch_one(
        conn,
        """
        WITH distinct_events AS (
            SELECT DISTINCT event_id
            FROM ingest.events_unpacked
        )
        SELECT
            (SELECT COUNT(*) FROM ingest.events_unpacked) AS calendar_rows,
            (SELECT COUNT(*) FROM distinct_events) AS distinct_events,
            (SELECT MIN(start_date) FROM ingest.events_unpacked) AS min_start_date,
            (SELECT MAX(end_date) FROM ingest.events_unpacked) AS max_end_date,
            (
                SELECT COUNT(*)
                FROM distinct_events AS e
                JOIN ingest.match_entries_payloads_raw AS m
                  ON m.event_id = e.event_id
            ) AS events_match_fetched,
            (
                SELECT COUNT(*)
                FROM distinct_events AS e
                JOIN ingest.match_entries_payloads_raw AS m
                  ON m.event_id = e.event_id
                WHERE m.received_count > 0
            ) AS events_with_matches,
            (
                SELECT COUNT(*)
                FROM ingest.match_entries_pending
            ) AS events_match_pending,
            (
                SELECT COUNT(*)
                FROM distinct_events AS e
                JOIN ingest.event_sponsors_raw AS s
                  ON s.event_id = e.event_id
            ) AS events_sponsor_fetched,
            (
                SELECT COUNT(*)
                FROM distinct_events AS e
                JOIN ingest.event_sponsors_raw AS s
                  ON s.event_id = e.event_id
                WHERE s.received_count > 0
            ) AS events_with_sponsors,
            (
                SELECT COUNT(*)
                FROM ingest.event_sponsors_pending
            ) AS events_sponsor_pending
        ;
        """,
    )

    # Matches
    matches_stats = _fetch_one(
        conn,
        """
        SELECT
            (SELECT COUNT(*) FROM ingest.match_entries_unpacked) AS total_matches,
            (
                SELECT COUNT(DISTINCT event_id)
                FROM ingest.match_entries_unpacked
            ) AS events_with_matches,
            (SELECT COUNT(*) FROM ingest.match_details_raw) AS details_fetched,
            (
                SELECT COUNT(*)
                FROM ingest.match_details_raw
                WHERE payload <> '{}'::jsonb
                  AND payload <> '[]'::jsonb
                  AND payload <> 'null'::jsonb
            ) AS details_non_empty,
            (
                SELECT COUNT(DISTINCT (event_id, document_code))
                FROM ingest.match_details_players_unpacked
            ) AS details_with_players,
            (SELECT COUNT(*) FROM ingest.match_details_pending) AS details_pending,
            (SELECT COUNT(*) FROM ingest.match_commentary_raw) AS commentary_fetched,
            (
                SELECT COUNT(*)
                FROM ingest.match_commentary_raw
                WHERE received_count > 0
            ) AS commentary_with_rows,
            (
                SELECT COUNT(*)
                FROM ingest.match_commentary_pending
            ) AS commentary_pending,
            (
                SELECT COUNT(*)
                FROM ingest.match_commentary_unpacked
            ) AS commentary_total_rows
        ;
        """,
    )

    # Players
    players_stats = _fetch_one(
        conn,
        """
        WITH seen_players AS (
            SELECT DISTINCT player_id
            FROM ingest.match_details_players_unpacked
        )
        SELECT
            (SELECT COUNT(*) FROM seen_players) AS seen_players,
            (
                SELECT COUNT(*)
                FROM seen_players AS sp
                JOIN ingest.player_details_raw AS r
                  ON r.player_id = sp.player_id
            ) AS seen_with_raw_details,
            (
                SELECT COUNT(*)
                FROM seen_players AS sp
                JOIN ingest.player_details_unpacked AS u
                  ON u.player_id = sp.player_id
            ) AS seen_with_unpacked_details,
            (
                SELECT COUNT(*)
                FROM ingest.player_details_pending
            ) AS details_pending,
            (SELECT COUNT(*) FROM ingest.player_details_raw) AS total_raw_details,
            (
                SELECT COUNT(*)
                FROM ingest.player_details_unpacked
            ) AS total_unpacked_details,
            (
                SELECT COUNT(*)
                FROM seen_players AS sp
                JOIN ingest.ittf_player_profiles AS i
                  ON i.player_id = sp.player_id
            ) AS seen_with_ittf_profile,
            (
                SELECT COUNT(*)
                FROM ingest.ittf_player_profiles_pending
            ) AS ittf_pending,
            (SELECT COUNT(*) FROM ingest.ittf_player_profiles) AS total_ittf_profiles
        ;
        """,
    )

    ittf_sources = _fetch_all(
        conn,
        """
        SELECT
            source,
            COUNT(*) AS row_count,
            COUNT(*) FILTER (
                WHERE style IS NOT NULL AND btrim(style) <> ''
            ) AS with_style,
            COUNT(*) FILTER (
                WHERE grip IS NOT NULL AND btrim(grip) <> ''
            ) AS with_grip,
            COUNT(*) FILTER (
                WHERE hand IS NOT NULL AND btrim(hand) <> ''
            ) AS with_hand
        FROM ingest.ittf_player_profiles
        GROUP BY source
        ORDER BY row_count DESC, source;
        """,
    )

    ingest_runs = _fetch_all(
        conn,
        """
        SELECT
            source,
            COUNT(*) AS total_runs,
            COUNT(*) FILTER (WHERE status = 'succeeded') AS succeeded,
            COUNT(*) FILTER (WHERE status = 'failed') AS failed,
            MAX(finished_at) AS last_finished_at
        FROM ingest.ingest_runs
        GROUP BY source
        ORDER BY total_runs DESC, source;
        """,
    )

    # Field completeness for unpacked tables
    sponsor_fields = _field_completeness_rows(
        conn,
        "ingest.event_sponsors_unpacked",
        [
            ("sponsor_type_id", "sponsor_type_id"),
            ("sponsor_type_name", "sponsor_type_name"),
            ("logo", "logo"),
            ("dark_logo", "dark_logo"),
            ("sponsor_link", "sponsor_link"),
        ],
    )

    commentary_fields = _field_completeness_rows(
        conn,
        "ingest.match_commentary_unpacked",
        [
            ("commentary_id", "commentary_id"),
            ("action_type", "action_type"),
            ("commentary_text", "commentary_text"),
            ("utc_date", "utc_date"),
            ("local_date", "local_date"),
            ("source_ts", "source_ts"),
        ],
    )

    player_fields = _field_completeness_rows(
        conn,
        "ingest.player_details_unpacked",
        [
            ("player_name", "player_name"),
            ("country_code", "country_code"),
            ("country_name", "country_name"),
            ("nationality_code", "nationality_code"),
            ("gender", "gender"),
            ("age", "age"),
            ("dob", "dob"),
            ("handedness", "handedness"),
            ("grip", "grip"),
            ("style", "style"),
            ("equipment_sponsor", "equipment_sponsor"),
            ("blade_type", "blade_type"),
            ("racket_covering_a", "racket_covering_a"),
            ("racket_covering_b", "racket_covering_b"),
            ("active_since", "active_since"),
            ("earnings_career", "earnings_career"),
            ("head_shot", "head_shot"),
            ("bio", "bio"),
        ],
    )

    ittf_fields = _field_completeness_rows(
        conn,
        "ingest.ittf_player_profiles",
        [
            ("style", "style"),
            ("grip", "grip"),
            ("hand", "hand"),
        ],
    )

    # Build sections
    sections: list[str] = []

    sections.append(
        "\n".join(
            [
                "# Database Data Summary",
                "",
                f"- **Generated at**: {generated_at}",
                f"- **Database**: `{db_meta.get('db_name', 'unknown')}` "
                f"({db_meta.get('db_size', 'unknown')})",
                f"- **Applied migrations**: "
                f"{_fmt_int(db_meta.get('migrations_applied'))}",
            ]
        )
    )

    # High-level coverage comparison across core entities
    fetched_years = int(years_stats.get("fetched_years", 0))
    years_with_events = int(years_stats.get("years_with_events", 0))
    distinct_events = int(events_stats.get("distinct_events", 0))
    events_match_fetched = int(events_stats.get("events_match_fetched", 0))
    events_with_matches = int(events_stats.get("events_with_matches", 0))
    events_sponsor_fetched = int(events_stats.get("events_sponsor_fetched", 0))
    events_with_sponsors = int(events_stats.get("events_with_sponsors", 0))

    total_matches = int(matches_stats.get("total_matches", 0))
    details_fetched = int(matches_stats.get("details_fetched", 0))
    details_non_empty = int(matches_stats.get("details_non_empty", 0))
    details_empty = max(details_fetched - details_non_empty, 0)
    details_with_players = int(matches_stats.get("details_with_players", 0))
    commentary_fetched = int(matches_stats.get("commentary_fetched", 0))
    commentary_with_rows = int(matches_stats.get("commentary_with_rows", 0))

    seen_players = int(players_stats.get("seen_players", 0))
    seen_with_raw_details = int(players_stats.get("seen_with_raw_details", 0))
    seen_with_unpacked_details = int(players_stats.get("seen_with_unpacked_details", 0))
    seen_with_ittf = int(players_stats.get("seen_with_ittf_profile", 0))
    total_ittf_profiles = int(players_stats.get("total_ittf_profiles", 0))
    commentary_total_rows = int(matches_stats.get("commentary_total_rows", 0))

    snapshot_rows = [
        [
            "Tournaments",
            _fmt_int(distinct_events),
            f"{_fmt_int(events_with_matches)} with matches; "
            f"{_fmt_int(events_with_sponsors)} with sponsors",
        ],
        [
            "Matches",
            _fmt_int(total_matches),
            f"{_fmt_int(details_non_empty)} with non-empty details "
            f"({_fmt_int(details_with_players)} with players); "
            f"{_fmt_int(commentary_with_rows)} with commentary "
            f"({_fmt_int(commentary_total_rows)} rows)",
        ],
        [
            "Players",
            _fmt_int(seen_players),
            f"{_fmt_int(seen_with_unpacked_details)} with WTT profile; "
            f"{_fmt_int(seen_with_ittf)} with ITTF profile "
            f"({_fmt_int(total_ittf_profiles)} total ITTF rows)",
        ],
    ]
    sections.append(
        "\n".join(
            [
                "## Executive Data Overview",
                "",
                _md_table(
                    ["Entity", "Actual Count", "Usable Data Snapshot"],
                    snapshot_rows,
                ),
            ]
        )
    )

    overview_rows = [
        _coverage_row(
            "Calendar years fetched -> have events",
            fetched_years,
            years_with_events,
            notes=f"range {years_stats.get('min_year')}-{years_stats.get('max_year')}, "
            f"{_fmt_int(years_stats.get('pending_years'))} pending",
        ),
        _coverage_row(
            "Tournaments -> match list fetched",
            distinct_events,
            events_match_fetched,
            notes=f"{_fmt_int(events_stats.get('events_match_pending'))} pending",
        ),
        _coverage_row(
            "Tournaments -> have matches (>0)",
            distinct_events,
            events_with_matches,
            notes="remaining fetched events returned [] or are pending",
        ),
        _coverage_row(
            "Tournaments -> sponsors fetched",
            distinct_events,
            events_sponsor_fetched,
            notes=f"{_fmt_int(events_stats.get('events_sponsor_pending'))} pending",
        ),
        _coverage_row(
            "Tournaments -> have sponsors (>0)",
            distinct_events,
            events_with_sponsors,
            notes="remaining fetched events had 0 sponsors or are pending",
        ),
        _coverage_row(
            "Matches -> match details fetched",
            total_matches,
            details_fetched,
            notes=f"{_fmt_int(matches_stats.get('details_pending'))} pending",
        ),
        _coverage_row(
            "Matches -> have non-empty details",
            total_matches,
            details_non_empty,
            notes=f"{_fmt_int(details_empty)} fetched rows returned empty {{}}",
        ),
        _coverage_row(
            "Matches -> have unpacked players",
            total_matches,
            details_with_players,
            notes="from match_details_players_unpacked",
        ),
        _coverage_row(
            "Matches -> commentary fetched",
            total_matches,
            commentary_fetched,
            notes=f"{_fmt_int(matches_stats.get('commentary_pending'))} pending",
        ),
        _coverage_row(
            "Matches -> have commentary (>0 rows)",
            total_matches,
            commentary_with_rows,
            notes=f"{_fmt_int(matches_stats.get('commentary_total_rows'))} total rows",
        ),
        _coverage_row(
            "Seen players -> WTT details fetched",
            seen_players,
            seen_with_raw_details,
            notes=f"{_fmt_int(players_stats.get('details_pending'))} pending",
        ),
        _coverage_row(
            "Seen players -> WTT profile unpacked",
            seen_players,
            seen_with_unpacked_details,
            notes="non-empty GetPlayers response",
        ),
        _coverage_row(
            "Seen players -> ITTF profile",
            seen_players,
            seen_with_ittf,
            notes=f"{_fmt_int(players_stats.get('ittf_pending'))} pending; "
            f"{_fmt_int(players_stats.get('total_ittf_profiles'))} total ITTF rows",
        ),
    ]
    sections.append(
        "\n".join(
            [
                "## High-Level Data Coverage (Has Data vs. Does Not Have Data)",
                "",
                _md_table(
                    ["Entity / Check", "Total", "Has Data", "No Data", "Notes"],
                    overview_rows,
                ),
            ]
        )
    )

    # Tournaments & Calendar details
    sections.append(
        "\n".join(
            [
                "## Tournaments & Calendar",
                "",
                f"- **Calendar years fetched**: {_fmt_int(fetched_years)} "
                f"({years_stats.get('min_year')}-{years_stats.get('max_year')}), "
                f"with **{_fmt_int(years_with_events)}** having events and "
                f"**{_fmt_int(years_stats.get('empty_years'))}** empty.",
                f"- **Calendar event rows**: "
                f"{_fmt_int(events_stats.get('calendar_rows'))} "
                f"unpacked from {_fmt_int(years_stats.get('raw_event_rows'))} "
                f"raw items.",
                f"- **Distinct tournaments (`event_id`)**: "
                f"{_fmt_int(distinct_events)} "
                f"(date span: {events_stats.get('min_start_date')} to "
                f"{events_stats.get('max_end_date')}).",
            ]
        )
    )

    # Sponsors field completeness
    sections.append(
        "\n".join(
            [
                "## Event Sponsors Field Completeness "
                "(`ingest.event_sponsors_unpacked`)",
                "",
                _md_table(
                    ["Field", "Total Rows", "Has Data", "Missing / Empty"],
                    sponsor_fields,
                ),
            ]
        )
    )

    # Commentary field completeness
    sections.append(
        "\n".join(
            [
                "## Match Commentary Field Completeness "
                "(`ingest.match_commentary_unpacked`)",
                "",
                _md_table(
                    ["Field", "Total Rows", "Has Data", "Missing / Empty"],
                    commentary_fields,
                ),
            ]
        )
    )

    # Players & ITTF enrichment
    ittf_extra = max(
        int(players_stats.get("total_ittf_profiles", 0)) - seen_with_ittf,
        0,
    )
    ittf_source_rows = [
        [
            str(r["source"]),
            _fmt_int(r["row_count"]),
            _fmt_int(r["with_style"]),
            _fmt_int(r["with_grip"]),
            _fmt_int(r["with_hand"]),
        ]
        for r in ittf_sources
    ]

    player_section = [
        "## Players & Profile Enrichment",
        "",
        f"- **Distinct players seen in matches**: {_fmt_int(seen_players)}",
        f"- **WTT `player_details_raw` rows**: "
        f"{_fmt_int(players_stats.get('total_raw_details'))} "
        f"(unpacked: {_fmt_int(players_stats.get('total_unpacked_details'))})",
        f"- **ITTF `ittf_player_profiles` rows**: "
        f"{_fmt_int(players_stats.get('total_ittf_profiles'))} "
        f"({_fmt_int(seen_with_ittf)} matched to seen players, "
        f"{_fmt_int(ittf_extra)} not yet seen in WTT matches)",
        "",
        "### WTT Player Details Completeness (`ingest.player_details_unpacked`)",
        "",
        _md_table(
            ["Field", "Total Rows", "Has Data", "Missing / Empty"],
            player_fields,
        ),
        "",
        "### ITTF Player Profiles Completeness (`ingest.ittf_player_profiles`)",
        "",
        _md_table(
            ["Field", "Total Rows", "Has Data", "Missing / Empty"],
            ittf_fields,
        ),
    ]
    if ittf_source_rows:
        player_section.extend(
            [
                "",
                "### ITTF Profiles by Source",
                "",
                _md_table(
                    ["Source", "Rows", "With Style", "With Grip", "With Hand"],
                    ittf_source_rows,
                ),
            ]
        )
    sections.append("\n".join(player_section))

    # Ingestion runs
    if ingest_runs:
        run_rows = []
        for r in ingest_runs:
            total = int(r["total_runs"])
            succ = int(r["succeeded"])
            fail = int(r["failed"])
            last_ts = r["last_finished_at"]
            last_str = (
                last_ts.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
                if last_ts is not None
                else "-"
            )
            run_rows.append(
                [
                    str(r["source"]),
                    _fmt_int(total),
                    f"{_fmt_int(succ)} ({_fmt_pct(succ, total)})",
                    f"{_fmt_int(fail)} ({_fmt_pct(fail, total)})",
                    last_str,
                ]
            )
        sections.append(
            "\n".join(
                [
                    "## Ingestion Run History (`ingest.ingest_runs`)",
                    "",
                    _md_table(
                        ["Source", "Total Runs", "Succeeded", "Failed", "Last Run"],
                        run_rows,
                    ),
                ]
            )
        )

    # Physical table inventory
    inv_rows = [
        [
            f"`{r['schema_name']}.{r['table_name']}`",
            _fmt_int(r["exact_rows"]),
            str(r["total_size"]),
        ]
        for r in table_stats
    ]
    sections.append(
        "\n".join(
            [
                "## Table Inventory (Row Counts & Disk Size)",
                "",
                _md_table(
                    ["Table", "Rows", "Total Size"],
                    inv_rows,
                ),
            ]
        )
    )

    return "\n\n".join(sections) + "\n"


def write_summary(output_path: Path = DEFAULT_OUTPUT_PATH) -> Path:
    """Connect to PostgreSQL, generate the summary, and write it to disk."""
    with get_connection() as conn:
        markdown = build_summary_markdown(conn)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    logger.info("Wrote database summary to %s", output_path)
    return output_path


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a Markdown summary of all data in PostgreSQL."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Output Markdown path (default: {DEFAULT_OUTPUT_PATH})",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-update-summary``."""
    configure_logging()
    args = _parse_args(argv)
    output_path = write_summary(args.output)
    print(f"Summary written to {output_path}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

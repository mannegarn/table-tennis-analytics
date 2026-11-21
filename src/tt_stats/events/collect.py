"""WTT event calendar: one payload per year into ``ingest_wtt.events_by_year_raw``.

Which years are asked for is derived in ``ingest_wtt.events_years_pending``
(ADR 0005): the current year and the next always, any other year only while its
payload is missing. A year with no tournaments is a success, not a failure: the
normal envelope with an empty ``rows`` list. Landing it stops the queue asking.

The fetch, land and record loop is shared: see ``tt_stats.core.collector``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from psycopg import Connection

from tt_stats.core.clients.wtt_api import WttClient
from tt_stats.core.collector import (
    Checked,
    Endpoint,
    PayloadShapeError,
    collector_main,
    object_rows,
    rows_error,
)
from tt_stats.core.config import Settings
from tt_stats.core.ingest_runs import ENDPOINT_WTT_EVENTCALENDAR
from tt_stats.events.land import land_events_by_year

DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

REQUIRED_EVENT_KEYS = (
    "EventId",
    "EventName",
    "EventType",
    "EventTypeId",
    "Country",
    "ContinentCode",
    "Subcontinent",
    "StartDateTime",
    "EndDateTime",
)

#: Current year first, then next year, then backfill newest first. The ORDER BY
#: is repeated here because filtering a view does not keep the view's order.
_PENDING_YEARS_SQL = """
WITH bounds AS (
    SELECT EXTRACT(
        YEAR FROM (current_timestamp AT TIME ZONE 'UTC')
    )::int AS current_year
)
SELECT p.year, p.reason
FROM ingest_wtt.events_years_pending AS p
CROSS JOIN bounds AS b
WHERE p.year >= %(start_year)s
ORDER BY
    (p.year = b.current_year) DESC,
    (p.year = b.current_year + 1) DESC,
    p.year DESC;
"""


def pending_years(
    connection: Connection[Any],
    start_year: int,
    limit: int | None = None,
) -> list[tuple[int, str]]:
    """Return ``(year, reason)`` pairs to request, from ``start_year`` onward."""
    rows = connection.execute(_PENDING_YEARS_SQL, {"start_year": start_year}).fetchall()
    queue = [(int(row["year"]), str(row["reason"])) for row in rows]
    return queue[:limit]


def check_payload(year: int, payload: object) -> Checked:
    """Check one calendar response: ``[{"rows": [...], "Count": n, ...}]``.

    ``rows`` must exist even when empty, or a renamed key would land as a quiet
    year. The number of rows must equal WTT's own ``Count``.
    """
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise PayloadShapeError("expected a list holding one envelope object")

    envelope = payload[0]
    rows = object_rows(envelope.get("rows"), what="event rows")
    count = _as_int(envelope.get("Count"))
    facts = {
        "source_final_count": count,
        "source_original_count": _as_int(envelope.get("Original_Count")),
    }

    error = rows_error(validate_rows(rows))
    if error is None and count != len(rows):
        error = f"count mismatch: source_final_count={count} received_count={len(rows)}"
    return Checked(len(rows), error=error, facts=facts)


def validate_rows(rows: Sequence[dict[str, Any]]) -> list[str]:
    """Return a list of contract problems, empty when every row is valid."""
    problems: list[str] = []

    for index, row in enumerate(rows):
        missing = [key for key in REQUIRED_EVENT_KEYS if key not in row]
        if missing:
            problems.append(f"row {index} missing keys: {', '.join(missing)}")
            continue

        event_id = row["EventId"]
        if not isinstance(event_id, int) or isinstance(event_id, bool):
            problems.append(f"row {index} EventId is not an int")

        for key in ("StartDateTime", "EndDateTime"):
            try:
                datetime.strptime(str(row[key]), DATE_FORMAT)
            except ValueError:
                problems.append(f"row {index} {key} is not in {DATE_FORMAT} format")

    return problems


def _as_int(value: object) -> int | None:
    """WTT sends counts as ints or numeric strings. Anything else is None."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


EVENTS = Endpoint[int](
    name="events",
    endpoint=ENDPOINT_WTT_EVENTCALENDAR,
    unit="year",
    found_label="events",
    fetch=WttClient.fetch_events_by_year,
    check=check_payload,
    raw_table="ingest_wtt.events_by_year_raw",
    unpacked_table="ingest_wtt.events_unpacked",
)


def _queue(
    connection: Connection[Any],
    settings: Settings,
    years: list[int] | None,
) -> Sequence[tuple[int, str | None]]:
    if years is not None:
        return [(year, None) for year in years]
    return pending_years(connection, settings.events_start_year)


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-events-collect``."""
    return collector_main(
        EVENTS,
        argv,
        description=(
            "Fetch WTT event calendars into ingest_wtt.events_by_year_raw. Without "
            "--years, the pending view decides: the current year, the next "
            "year, and any year whose payload is missing."
        ),
        ids_flag="--years",
        ids_help="Explicit years to fetch, bypassing the pending view.",
        queue=_queue,
        land=land_events_by_year,
    )


if __name__ == "__main__":
    raise SystemExit(main())

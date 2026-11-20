"""WTT match lists: one payload per event into
``ingest_wtt.match_entries_payloads_raw``.

The endpoint returns the whole match list for one tournament as a bare JSON
array. An empty answer, ``[]``, is a success: many events, and everything before
late 2021, have no matches. Which events are asked for is derived in
``ingest_wtt.match_entries_pending`` (ADR 0005), never stored.

The fetch, land and record loop is shared: see ``tt_stats.core.collector``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from psycopg import Connection

from tt_stats.core.clients.wtt_api import WttClient
from tt_stats.core.collector import (
    Checked,
    Endpoint,
    collector_main,
    object_rows,
    rows_error,
)
from tt_stats.core.config import Settings
from tt_stats.core.ingest_runs import ENDPOINT_WTT_MATCH_ENTRIES
from tt_stats.matches.land import land_match_entries

#: Keys every match row must carry so it can be unpacked and routed.
#: ``id`` / ``iD``, ``startDateLocal``, ``subEventType`` and ``fullResults``
#: stay in the raw JSON payload for dbt (ADR 0003 / ADR 0010), so their
#: absence or upstream quirks do not block landing.
REQUIRED_MATCH_KEYS = (
    "eventId",
    "documentCode",
)

#: Live events first, then finished events by most recent end date.
_PENDING_EVENTS_SQL = """
SELECT p.event_id, p.reason
FROM ingest_wtt.match_entries_pending AS p
ORDER BY
    (p.reason = 'live') DESC,
    GREATEST(p.start_date, p.end_date) DESC,
    p.event_id;
"""


def pending_events(
    connection: Connection[Any],
    limit: int | None = None,
) -> list[tuple[int, str]]:
    """Return ``(event_id, reason)`` pairs to fetch, in queue order."""
    rows = connection.execute(_PENDING_EVENTS_SQL).fetchall()
    queue = [(int(row["event_id"]), str(row["reason"])) for row in rows]
    return queue[:limit]


def check_payload(event_id: int, payload: object) -> Checked:
    """Check one match list: a bare array of match objects for ``event_id``."""
    rows = object_rows(payload, what="match rows")
    return Checked(len(rows), error=rows_error(validate_rows(rows, event_id=event_id)))


def validate_rows(
    rows: Sequence[dict[str, Any]],
    *,
    event_id: int,
) -> list[str]:
    """Return a list of contract problems, empty when every row is valid.

    The ``eventId`` check is the important one: it proves the request returned
    the event that was asked for. Without it, a wrong query parameter would land
    one event's matches under another event's id and nothing else would notice.
    """
    problems: list[str] = []

    for index, row in enumerate(rows):
        missing = [key for key in REQUIRED_MATCH_KEYS if key not in row]
        if missing:
            problems.append(f"row {index} missing keys: {', '.join(missing)}")
            continue

        document_code = row["documentCode"]
        if not isinstance(document_code, str) or not document_code.strip():
            problems.append(f"row {index} has no non-empty documentCode")

        if str(row["eventId"]) != str(event_id):
            problems.append(
                f"row {index} eventId {row['eventId']!r} does not match "
                f"requested event {event_id}"
            )

    return problems


MATCH_ENTRIES = Endpoint[int](
    name="match_entries",
    endpoint=ENDPOINT_WTT_MATCH_ENTRIES,
    unit="event",
    found_label="matches",
    fetch=WttClient.fetch_match_entries,
    check=check_payload,
    raw_table="ingest_wtt.match_entries_payloads_raw",
    unpacked_table="ingest_wtt.match_entries_unpacked",
)


def _queue(
    connection: Connection[Any],
    _settings: Settings,
    event_ids: list[int] | None,
) -> Sequence[tuple[int, str | None]]:
    if event_ids is not None:
        return [(event_id, None) for event_id in event_ids]
    return pending_events(connection)


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-matches-collect``."""
    return collector_main(
        MATCH_ENTRIES,
        argv,
        description=(
            "Fetch WTT match lists into ingest_wtt.match_entries_payloads_raw. "
            "Without --event-ids, the pending view decides: live events, then "
            "events never fetched, then events whose snapshot predates their "
            "final day."
        ),
        ids_flag="--event-ids",
        ids_help="Explicit event ids to fetch, bypassing the pending view.",
        queue=_queue,
        land=land_match_entries,
    )


if __name__ == "__main__":
    raise SystemExit(main())

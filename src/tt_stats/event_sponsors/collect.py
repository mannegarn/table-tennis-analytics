"""WTT event sponsors: one payload per event into ``ingest.event_sponsors_raw``.

``GetEventEquipmentwithLogo/{event_id}`` returns the sponsor and equipment list
for one tournament as a bare JSON array. An empty ``[]`` is a valid observation
and is landed, so finished events are not requested again. Which events are
asked for is derived in ``ingest.event_sponsors_pending`` (ADR 0005).

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
from tt_stats.core.ingest_runs import SOURCE_WTT_EVENT_SPONSORS
from tt_stats.event_sponsors.land import land_event_sponsors

#: Keys every sponsor row must carry so we can verify the response belongs to
#: the requested event before landing the raw payload.
REQUIRED_SPONSOR_KEYS = (
    "eventId",
    "eventSponsorId",
)

#: Live events first, then finished events by most recent end date.
_PENDING_EVENTS_SQL = """
SELECT p.event_id, p.reason
FROM ingest.event_sponsors_pending AS p
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
    """Check one sponsor list: a bare array of sponsor objects for ``event_id``."""
    rows = object_rows(payload, what="sponsor rows")
    return Checked(len(rows), error=rows_error(validate_rows(rows, event_id=event_id)))


def validate_rows(
    rows: Sequence[dict[str, Any]],
    *,
    event_id: int,
) -> list[str]:
    """Return a list of contract problems, empty when every row is valid."""
    problems: list[str] = []

    for index, row in enumerate(rows):
        missing = [key for key in REQUIRED_SPONSOR_KEYS if key not in row]
        if missing:
            problems.append(f"row {index} missing keys: {', '.join(missing)}")
            continue

        if row["eventSponsorId"] is None:
            problems.append(f"row {index} has no eventSponsorId")

        if str(row["eventId"]) != str(event_id):
            problems.append(
                f"row {index} eventId {row['eventId']!r} does not match "
                f"requested event {event_id}"
            )

    return problems


EVENT_SPONSORS = Endpoint[int](
    name="event_sponsors",
    source=SOURCE_WTT_EVENT_SPONSORS,
    unit="event",
    found_label="sponsors",
    fetch=WttClient.fetch_event_sponsors,
    check=check_payload,
    raw_table="ingest.event_sponsors_raw",
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
    """Console entry point used by ``tt-event-sponsors-collect``."""
    return collector_main(
        EVENT_SPONSORS,
        argv,
        description=(
            "Fetch WTT event sponsors into ingest.event_sponsors_raw. "
            "Without --event-ids, the pending view decides: live events, then "
            "events never fetched, then events whose snapshot predates their "
            "final day."
        ),
        ids_flag="--event-ids",
        ids_help="Explicit event ids to fetch, bypassing the pending view.",
        queue=_queue,
        land=land_event_sponsors,
    )


if __name__ == "__main__":
    raise SystemExit(main())

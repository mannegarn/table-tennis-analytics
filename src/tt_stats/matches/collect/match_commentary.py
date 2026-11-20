"""WTT match commentary: one payload per match into ``ingest_wtt.match_commentary_raw``.

The endpoint returns a double-encoded JSON string holding an array of
commentary actions, or an empty array or string when a match has none. The
decoded array is what lands, a chunk per transaction. A 404 lands as ``[]`` so
the queue stops asking. Which matches are asked for is derived in
``ingest_wtt.match_commentary_pending`` (ADR 0005).

The fetch, land and record loop is shared: see ``tt_stats.core.collector``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from psycopg import Connection

from tt_stats.core.collector import (
    Checked,
    Endpoint,
    PayloadShapeError,
    collector_main,
    object_rows,
)
from tt_stats.core.config import Settings
from tt_stats.core.ingest_runs import ENDPOINT_WTT_MATCH_COMMENTARY
from tt_stats.matches.collect.match_details import MatchKey, match_entity_key
from tt_stats.matches.land import land_match_commentary_chunk

#: Live events first, then by most recent end date. ``event_ids`` NULL means
#: the whole queue.
_PENDING_MATCHES_SQL = """
SELECT p.event_id, p.document_code, p.reason
FROM ingest_wtt.match_commentary_pending AS p
WHERE %(event_ids)s::int[] IS NULL OR p.event_id = ANY(%(event_ids)s::int[])
ORDER BY
    (p.reason = 'live') DESC,
    GREATEST(p.start_date, p.end_date) DESC,
    p.event_id,
    p.document_code;
"""


def pending_matches(
    connection: Connection[Any],
    limit: int | None = None,
    *,
    event_ids: Sequence[int] | None = None,
) -> list[tuple[MatchKey, str]]:
    """Return ``((event_id, document_code), reason)`` pairs in queue order."""
    rows = connection.execute(
        _PENDING_MATCHES_SQL,
        {"event_ids": None if event_ids is None else list(event_ids)},
    ).fetchall()
    queue = [
        ((int(row["event_id"]), str(row["document_code"])), str(row["reason"]))
        for row in rows
    ]
    return queue[:limit]


def decode_commentary_payload(payload: object) -> list[dict[str, Any]]:
    """Unwrap the double-encoded string (if any) and return the list of rows."""
    if isinstance(payload, str):
        if not payload.strip():
            return []
        try:
            payload = json.loads(payload)
        except ValueError as error:
            raise PayloadShapeError(
                f"string payload is not valid inner JSON: {error}"
            ) from error

    if payload is None:
        return []
    return object_rows(payload, what="commentary rows")


def check_payload(_key: MatchKey, payload: object) -> Checked:
    """Decode one commentary payload; the decoded rows are what lands."""
    rows = decode_commentary_payload(payload)
    return Checked(len(rows), payload=rows)


MATCH_COMMENTARY = Endpoint[MatchKey](
    name="match_commentary",
    endpoint=ENDPOINT_WTT_MATCH_COMMENTARY,
    unit="match",
    found_label="lines",
    fetch=lambda client, key: client.fetch_match_commentary(*key),
    check=check_payload,
    raw_table="ingest_wtt.match_commentary_raw",
    entity_key=match_entity_key,
    not_found_payload=[],
)


def _queue(
    connection: Connection[Any],
    _settings: Settings,
    event_ids: list[int] | None,
) -> Sequence[tuple[MatchKey, str | None]]:
    return pending_matches(connection, event_ids=event_ids)


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-match-commentary-collect``."""
    return collector_main(
        MATCH_COMMENTARY,
        argv,
        description=(
            "Fetch WTT match commentary into ingest_wtt.match_commentary_raw, "
            "committed by chunk."
        ),
        ids_flag="--event-ids",
        ids_help="Optional event ids to filter the pending match_commentary queue.",
        queue=_queue,
        land_chunk=land_match_commentary_chunk,
    )


if __name__ == "__main__":
    raise SystemExit(main())

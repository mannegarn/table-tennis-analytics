"""WTT matchcentre: one payload per match into ``ingest_wtt.match_details_raw``.

A full backfill is ~200k matches, so each chunk is landed in one transaction
(raw payload plus a minimal player-id unpack) rather than one per match. A 404
means WTT has no detail for the match; it lands as ``{}`` so the queue stops
asking. Which matches are asked for is derived in
``ingest_wtt.match_details_pending`` (ADR 0005).

The fetch, land and record loop is shared: see ``tt_stats.core.collector``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from psycopg import Connection

from tt_stats.core.collector import Checked, Endpoint, PayloadShapeError, collector_main
from tt_stats.core.config import Settings
from tt_stats.core.ingest_runs import ENDPOINT_WTT_MATCH_DETAILS
from tt_stats.matches.land import land_match_details_chunk

MatchKey = tuple[int, str]

#: Live events first, then by most recent end date. ``event_ids`` NULL means
#: the whole queue.
_PENDING_MATCHES_SQL = """
SELECT p.event_id, p.document_code, p.reason
FROM ingest_wtt.match_details_pending AS p
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


def check_payload(key: MatchKey, payload: object) -> Checked:
    """Check one matchcentre object and count its distinct players."""
    event_id, document_code = key
    if not isinstance(payload, dict):
        raise PayloadShapeError(
            f"expected a match object, got {type(payload).__name__}"
        )

    if str(payload.get("eventId")) != str(event_id):
        return Checked(
            0,
            error=f"eventId {payload.get('eventId')!r} does not match requested "
            f"event {event_id}",
        )
    if str(payload.get("documentCode")) != document_code:
        return Checked(
            0,
            error=f"documentCode {payload.get('documentCode')!r} does not match "
            f"requested {document_code!r}",
        )

    # "competitiors" is WTT's spelling.
    player_ids = {
        str(player["playerId"])
        for competitor in _dicts(payload.get("competitiors"))
        for player in _dicts(competitor.get("players"))
        if player.get("playerId") is not None
    }
    return Checked(len(player_ids))


def _dicts(value: object) -> list[dict[str, Any]]:
    """The objects in ``value`` when it is a list; tolerant, unlike object_rows."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def match_entity_key(key: MatchKey) -> str:
    """``3257:TTEWSINGLES...``: how a match is written in runs and logs."""
    return f"{key[0]}:{key[1]}"


MATCH_DETAILS = Endpoint[MatchKey](
    name="match_details",
    endpoint=ENDPOINT_WTT_MATCH_DETAILS,
    unit="match",
    found_label="players",
    fetch=lambda client, key: client.fetch_match_details(*key),
    check=check_payload,
    raw_table="ingest_wtt.match_details_raw",
    unpacked_table="ingest_wtt.match_details_players_unpacked",
    entity_key=match_entity_key,
    not_found_payload={},
)


def _queue(
    connection: Connection[Any],
    _settings: Settings,
    event_ids: list[int] | None,
) -> Sequence[tuple[MatchKey, str | None]]:
    return pending_matches(connection, event_ids=event_ids)


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-match-details-collect``."""
    return collector_main(
        MATCH_DETAILS,
        argv,
        description=(
            "Fetch WTT matchcentre payloads into ingest_wtt.match_details_raw, "
            "landing and recording runs at the end of each chunk."
        ),
        ids_flag="--event-ids",
        ids_help="Optional event ids to filter the pending match_details queue.",
        queue=_queue,
        land_chunk=land_match_details_chunk,
    )


if __name__ == "__main__":
    raise SystemExit(main())

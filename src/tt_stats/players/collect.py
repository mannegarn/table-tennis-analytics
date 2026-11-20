"""WTT player profiles: one payload per player into ``ingest_wtt.player_details_raw``.

Each chunk lands in one transaction. A 404 means WTT has no profile; it lands as
``{"Result": []}`` so the queue stops asking. Which players are asked for is
derived in ``ingest_wtt.player_details_pending`` (ADR 0005).

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
    PayloadShapeError,
    collector_main,
    object_rows,
)
from tt_stats.core.config import Settings
from tt_stats.core.ingest_runs import ENDPOINT_WTT_PLAYER_DETAILS
from tt_stats.players.land import land_player_details_chunk

_PENDING_PLAYERS_SQL = """
SELECT p.player_id, p.reason
FROM ingest_wtt.player_details_pending AS p
ORDER BY p.player_id;
"""


def pending_players(
    connection: Connection[Any],
    limit: int | None = None,
) -> list[tuple[int, str]]:
    """Return ``(player_id, reason)`` pairs in queue order."""
    rows = connection.execute(_PENDING_PLAYERS_SQL).fetchall()
    queue = [(int(row["player_id"]), str(row["reason"])) for row in rows]
    return queue[:limit]


def check_payload(player_id: int, payload: object) -> Checked:
    """Check one ``/Players/GetPlayers`` envelope and count its profiles."""
    if not isinstance(payload, dict):
        raise PayloadShapeError(
            f"expected a player envelope object, got {type(payload).__name__}"
        )

    result = payload.get("Result")
    if result is None:
        return Checked(0)
    profiles = object_rows(result, what="profiles in Result")

    returned_id = profiles[0].get("IttfId") if profiles else None
    if returned_id is not None and str(returned_id) != str(player_id):
        return Checked(
            0,
            error=f"IttfId {returned_id!r} does not match requested player {player_id}",
        )
    return Checked(len(profiles))


PLAYER_DETAILS = Endpoint[int](
    name="player_details",
    endpoint=ENDPOINT_WTT_PLAYER_DETAILS,
    unit="player",
    found_label="profiles",
    fetch=WttClient.fetch_player_details,
    check=check_payload,
    raw_table="ingest_wtt.player_details_raw",
    not_found_payload={"Result": []},
)


def _queue(
    connection: Connection[Any],
    _settings: Settings,
    player_ids: list[int] | None,
) -> Sequence[tuple[int, str | None]]:
    if player_ids is not None:
        return [(player_id, None) for player_id in player_ids]
    return pending_players(connection)


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-player-details-collect``."""
    return collector_main(
        PLAYER_DETAILS,
        argv,
        description=(
            "Fetch WTT player details into ingest_wtt.player_details_raw, "
            "committed by chunk."
        ),
        ids_flag="--player-ids",
        ids_help="Explicit player IDs to fetch, bypassing the pending view.",
        queue=_queue,
        land_chunk=land_player_details_chunk,
    )


if __name__ == "__main__":
    raise SystemExit(main())

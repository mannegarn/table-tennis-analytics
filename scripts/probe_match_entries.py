"""Scratch probe for the WTT match-entries endpoint.

Local testing only: not imported by the package, not shipped in the wheel, and
the captured payloads are git-ignored. Its job is to answer, for any event id:

* is the response a bare list, or an envelope with a ``rows`` key?
* which keys does a match row actually carry?
* does an old event (pre-2021) return anything at all?

That last question decides whether the match-entries queue needs a year floor
before the first full fan-out, so probing a handful of ids is much cheaper than
discovering it 2000 requests into a backfill.

Named after the entity, not the call, so one vocabulary runs through the
repository: match entries, never "event matches".

Run it with::

    uv run python scripts/probe_match_entries.py
    uv run python scripts/probe_match_entries.py --event-id 3098 921 51 928
"""

import argparse
import json
from pathlib import Path
from typing import Any, cast

import httpx

#: A recent event with 306 matches, five sub-events. Useful as a control.
DEFAULT_EVENT_ID = 3098

_BASE_URL = "https://wttwebsiteprodapi-liveevents.trafficmanager.net"
URL = f"{_BASE_URL}/api/cms/GetOfficialResult"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.worldtabletennis.com/",
    "Origin": "https://www.worldtabletennis.com",
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 11.0; Surface Duo) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Mobile Safari/537.36"
    ),
    "secapimkey": "S_WTT_882jjh7basdj91834783mds8j2jsd81",
}

#: ``DocumentCode=TTE`` is what the old implementation used for every event.
DOCUMENT_CODE = "TTE"


def _say(message: str) -> None:
    """Print one line. A probe is a script, not application logging."""
    print(message)  # noqa: T201


def probe(client: httpx.Client, event_id: int) -> int:
    """Fetch one event's match list, save it if it has rows, return the count."""
    response = client.get(
        URL,
        params={"EventId": str(event_id), "DocumentCode": DOCUMENT_CODE},
        headers=HEADERS,
    )
    response.raise_for_status()

    payload: Any = response.json()
    rows: list[Any] = cast("list[Any]", payload) if isinstance(payload, list) else []

    if rows:
        target = Path(__file__).with_name(f"match_entries_{event_id}.json")
        target.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        first = cast("dict[str, Any]", rows[0]) if isinstance(rows[0], dict) else {}
        keys = ", ".join(sorted(first))
        _say(
            f"{event_id} | {response.status_code} | rows={len(rows):4d} | "
            f"{len(response.content):6d} B -> {target.name}"
        )
        _say(f"         keys: {keys}")
    else:
        shape = "list" if isinstance(payload, list) else type(payload).__name__
        _say(
            f"{event_id} | {response.status_code} | rows=   0 | "
            f"{len(response.content):6d} B | empty ({shape})"
        )

    return len(rows)


def main(argv: list[str] | None = None) -> int:
    """Probe each requested event id and print a one-line summary per event."""
    parser = argparse.ArgumentParser(
        description="Probe GetOfficialResult for one or more event ids."
    )
    parser.add_argument(
        "--event-id",
        nargs="+",
        type=int,
        default=[DEFAULT_EVENT_ID],
        help=f"Event ids to probe. Default: {DEFAULT_EVENT_ID}.",
    )
    args = parser.parse_args(argv)

    event_ids: list[int] = cast("list[int]", args.event_id)
    _say(f"probing {len(event_ids)} events: {', '.join(map(str, event_ids))}")

    total = 0
    populated = 0
    with httpx.Client(timeout=60.0) as client:
        for event_id in event_ids:
            try:
                rows = probe(client, event_id)
            except httpx.HTTPError as error:
                _say(f"{event_id} | ERROR | {type(error).__name__}: {error}")
                continue
            total += rows
            populated += 1 if rows else 0

    _say("")
    _say(f"events probed     : {len(event_ids)}")
    _say(f"events with rows  : {populated}")
    _say(f"total match rows  : {total}")
    _say(f"mean rows per hit : {total / populated if populated else 0:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

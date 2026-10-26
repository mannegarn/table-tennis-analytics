"""Route builders for the WTT website API.

The API expects a JSON body whose ``custom_filter`` value is itself a JSON
string. That quirk is preserved here and covered by a unit test, because
guessing it would produce an empty or rejected response.
"""

import json
from typing import Any

WTT_CALENDAR_URL = (
    "https://wtt-website-api-prod-3-frontdoor-bddnb2haduafdze9.a01.azurefd.net"
    "/api/eventcalendar"
)

_COMMON_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "origin": "https://www.worldtabletennis.com",
    "referer": "https://www.worldtabletennis.com/",
    "accept-language": "en-GB,en;q=0.9",
    "cache-control": "no-cache",
    "content-type": "application/json",
    "dnt": "1",
    "pragma": "no-cache",
    "priority": "u=1, i",
    "sec-ch-ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"Android"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "cross-site",
}


def get_events_year_route(
    year: int, *, api_key: str, user_agent: str
) -> dict[str, Any]:
    """Build the POST request for one year of the WTT event calendar.

    Args:
        year: Calendar year to request.
        api_key: Value for the ``secapimkey`` header.
        user_agent: User agent advertised to the site.

    Returns:
        A mapping with ``method``, ``url``, ``json_payload`` and ``headers``.
    """
    inner_filter: list[dict[str, str]] = [
        {
            "name": "StartDateTime",
            "value": str(year),
            "custom_handling": "multimatch_year_or_filter",
            "condition": "or_start",
        },
        {
            "name": "FromStartDate",
            "value": str(year),
            "custom_handling": "multimatch_year_or_filter",
            "condition": "or_end",
        },
    ]

    return {
        "method": "POST",
        "url": WTT_CALENDAR_URL,
        "json_payload": {"custom_filter": json.dumps(inner_filter)},
        "headers": {
            **_COMMON_HEADERS,
            "secapimkey": api_key,
            "user-agent": user_agent,
        },
    }

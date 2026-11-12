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

#: A different host from the calendar: this one serves the live-results API.
_LIVE_EVENTS_BASE_URL = "https://wttwebsiteprodapi-liveevents.trafficmanager.net"
WTT_MATCH_ENTRIES_URL = f"{_LIVE_EVENTS_BASE_URL}/api/cms/GetOfficialResult"

#: Frontdoor host serving the event equipment / sponsor logo endpoint.
_VM_FRONTDOOR_BASE_URL = (
    "https://wtt-website-api-vm-frontdoor-hhaec5epbhdyfugz.a01.azurefd.net"
)
WTT_EVENT_SPONSORS_URL_PREFIX = (
    f"{_VM_FRONTDOOR_BASE_URL}/liveeventsapi/api/cms/GetEventEquipmentwithLogo"
)

#: Every match-list request uses the same document code. Measured against a
#: 2025 event (306 rows) and late-2021 events (215-368 rows): one code covers
#: men's, women's, doubles and mixed draws.
MATCH_ENTRIES_DOCUMENT_CODE = "TTE"

DEFAULT_WTT_API_KEY = "S_WTT_882jjh7basdj91834783mds8j2jsd81"
DEFAULT_WTT_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 11.0; Surface Duo) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Mobile Safari/537.36"
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
    year: int,
    *,
    api_key: str = DEFAULT_WTT_API_KEY,
    user_agent: str = DEFAULT_WTT_USER_AGENT,
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


def get_match_entries_route(
    event_id: int,
    *,
    api_key: str = DEFAULT_WTT_API_KEY,
    user_agent: str = DEFAULT_WTT_USER_AGENT,
) -> dict[str, Any]:
    """Build the GET request for one event's match list.

    Unlike the calendar, this endpoint takes query parameters, and its response
    is a bare JSON array of match rows rather than a ``rows`` envelope.

    Args:
        event_id: WTT EventId to fetch the match list for.
        api_key: Value for the ``secapimkey`` header.
        user_agent: User agent advertised to the site.

    Returns:
        A mapping with ``method``, ``url``, ``params`` and ``headers``.
    """
    return {
        "method": "GET",
        "url": WTT_MATCH_ENTRIES_URL,
        "params": {
            "EventId": str(event_id),
            "DocumentCode": MATCH_ENTRIES_DOCUMENT_CODE,
        },
        "headers": {
            **_COMMON_HEADERS,
            "secapimkey": api_key,
            "user-agent": user_agent,
        },
    }


def get_event_sponsors_route(
    event_id: int,
    *,
    api_key: str = DEFAULT_WTT_API_KEY,
    user_agent: str = DEFAULT_WTT_USER_AGENT,
) -> dict[str, Any]:
    """Build the GET request for one event's sponsor and equipment list.

    The event id is part of the URL path rather than a query parameter, and the
    response is a bare JSON array of sponsor objects.

    Args:
        event_id: WTT EventId to fetch sponsors for.
        api_key: Value for the ``secapimkey`` header.
        user_agent: User agent advertised to the site.

    Returns:
        A mapping with ``method``, ``url`` and ``headers``.
    """
    return {
        "method": "GET",
        "url": f"{WTT_EVENT_SPONSORS_URL_PREFIX}/{event_id}",
        "headers": {
            **_COMMON_HEADERS,
            "secapimkey": api_key,
            "user-agent": user_agent,
        },
    }

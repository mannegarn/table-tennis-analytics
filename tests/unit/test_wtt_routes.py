"""Unit tests for the WTT route builders. No network involved."""

import json

from tt_stats.core.clients.wtt_routes import get_events_year_route


def test_calendar_route_is_a_post_to_the_eventcalendar_endpoint() -> None:
    route = get_events_year_route(2023, api_key="test-key", user_agent="test-agent")

    assert route["method"] == "POST"
    assert route["url"].endswith("/api/eventcalendar")


def test_calendar_filter_is_json_encoded_inside_the_payload() -> None:
    route = get_events_year_route(2023, api_key="test-key", user_agent="test-agent")

    raw_filter = route["json_payload"]["custom_filter"]
    assert isinstance(raw_filter, str)

    decoded = json.loads(raw_filter)
    assert [item["name"] for item in decoded] == [
        "StartDateTime",
        "FromStartDate",
    ]
    assert [item["value"] for item in decoded] == ["2023", "2023"]


def test_calendar_headers_carry_auth_and_user_agent() -> None:
    route = get_events_year_route(2023, api_key="test-key", user_agent="test-agent")

    assert route["headers"]["secapimkey"] == "test-key"
    assert route["headers"]["user-agent"] == "test-agent"

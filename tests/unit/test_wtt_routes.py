"""Unit tests for the WTT route builders. No network involved."""

import json

from tt_stats.core.clients.wtt_routes import (
    get_event_sponsors_route,
    get_events_year_route,
    get_match_details_route,
    get_match_entries_route,
)


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


def test_match_entries_route_is_a_get_to_official_results() -> None:
    route = get_match_entries_route(3098, api_key="test-key", user_agent="test-agent")

    assert route["method"] == "GET"
    assert route["url"].endswith("/api/cms/GetOfficialResult")
    assert "json_payload" not in route


def test_match_entries_params_carry_the_event_and_document_code() -> None:
    """EventId is a string on the wire, and DocumentCode is always TTE."""
    route = get_match_entries_route(3098, api_key="test-key", user_agent="test-agent")

    assert route["params"] == {"EventId": "3098", "DocumentCode": "TTE"}


def test_match_entries_headers_carry_auth_and_user_agent() -> None:
    route = get_match_entries_route(3098, api_key="test-key", user_agent="test-agent")

    assert route["headers"]["secapimkey"] == "test-key"
    assert route["headers"]["user-agent"] == "test-agent"


def test_event_sponsors_route_is_a_get_with_event_id_in_the_path() -> None:
    route = get_event_sponsors_route(3238, api_key="test-key", user_agent="test-agent")

    assert route["method"] == "GET"
    assert route["url"].endswith(
        "/liveeventsapi/api/cms/GetEventEquipmentwithLogo/3238"
    )
    assert "params" not in route
    assert "json_payload" not in route


def test_event_sponsors_headers_carry_auth_and_user_agent() -> None:
    route = get_event_sponsors_route(3238, api_key="test-key", user_agent="test-agent")

    assert route["headers"]["secapimkey"] == "test-key"
    assert route["headers"]["user-agent"] == "test-agent"


def test_match_details_route_is_a_get_with_event_and_document_in_path() -> None:
    code = "TTEWSINGLES-----------RND1002700----------"
    route = get_match_details_route(
        3257, code, api_key="test-key", user_agent="test-agent"
    )

    assert route["method"] == "GET"
    assert route["url"].endswith(f"/matchdata/matchcentre/3257/{code}.json")
    assert "params" not in route
    assert "json_payload" not in route
    assert route["headers"]["secapimkey"] == "test-key"
    assert route["headers"]["user-agent"] == "test-agent"

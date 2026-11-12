"""Unit tests for the async WTT client. Mock transports only, no network."""

import asyncio
import time
from typing import Any

import httpx
import pytest

from tt_stats.core.clients.wtt_api import (
    WttClient,
    WttPayloadError,
    WttRequestError,
)
from tt_stats.core.config import Settings


def _settings(**overrides: Any) -> Settings:
    """Build isolated settings that never read the real .env file."""
    values: dict[str, Any] = {
        "postgres_password": "test",
        "max_retry_attempts": 3,
        "retry_wait_min_seconds": 0.001,
        "retry_wait_max_seconds": 0.001,
        "request_jitter_seconds": 0.0,
    }
    values.update(overrides)
    return Settings(**values)


async def test_fetch_match_entries_sends_the_event_and_document_code() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[{"id": 1, "eventId": "3098"}])

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        response = await client.fetch_match_entries(3098)

    request = seen[0]
    assert request.method == "GET"
    assert request.url.params["EventId"] == "3098"
    assert request.url.params["DocumentCode"] == "TTE"
    assert response.status_code == 200
    assert response.data == [{"id": 1, "eventId": "3098"}]


async def test_fetch_match_entries_accepts_an_empty_list() -> None:
    """``[]`` is a real answer: some events have no matches at all."""

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        response = await client.fetch_match_entries(3098)

    assert response.data == []
    assert response.status_code == 200


async def test_fetch_event_sponsors_puts_event_id_in_the_url_path() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[{"eventId": 3238, "eventSponsorId": 5845}],
        )

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        response = await client.fetch_event_sponsors(3238)

    request = seen[0]
    assert request.method == "GET"
    assert request.url.path.endswith(
        "/liveeventsapi/api/cms/GetEventEquipmentwithLogo/3238"
    )
    assert dict(request.url.params) == {}
    assert response.status_code == 200
    assert response.data == [{"eventId": 3238, "eventSponsorId": 5845}]


async def test_fetch_event_sponsors_accepts_an_empty_list() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        response = await client.fetch_event_sponsors(3238)

    assert response.data == []
    assert response.status_code == 200


async def test_fetch_match_details_puts_event_and_document_in_path() -> None:
    seen: list[httpx.Request] = []
    code = "TTEWSINGLES-----------RND1002700----------"

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"eventId": "3257", "documentCode": code})

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        response = await client.fetch_match_details(3257, code)

    request = seen[0]
    assert request.method == "GET"
    assert request.url.path.endswith(f"/matchdata/matchcentre/3257/{code}.json")
    assert dict(request.url.params) == {}
    assert response.status_code == 200
    assert response.data == {"eventId": "3257", "documentCode": code}


async def test_fetch_match_commentary_puts_event_and_document_in_path() -> None:
    seen: list[httpx.Request] = []
    code = "TTEWSINGLES-----------RND1002700----------"

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json="[]")

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        response = await client.fetch_match_commentary(3257, code)

    request = seen[0]
    assert request.method == "GET"
    assert request.url.path.endswith(f"/primary/api/match/commentary/3257/{code}")
    assert dict(request.url.params) == {}
    assert response.status_code == 200
    assert response.data == "[]"


async def test_get_retries_a_transient_failure_then_succeeds() -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json=[])

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        await client.fetch_match_entries(3098)

    assert calls == 2


async def test_get_does_not_retry_a_client_error() -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, json={"error": "not found"})

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(WttRequestError) as caught:
            await client.fetch_match_entries(3098)

    assert caught.value.status_code == 404
    assert calls == 1


async def test_retries_429_then_succeeds() -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": "slow down"})
        return httpx.Response(200, json=[{"rows": []}])

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        response = await client.fetch_events_by_year(2023)

    assert response.status_code == 200
    assert calls == 2


async def test_does_not_retry_client_errors() -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": "bad request"})

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(WttRequestError) as error:
            await client.fetch_events_by_year(2023)

    assert error.value.status_code == 400
    assert calls == 1


async def test_semaphore_caps_in_flight_requests() -> None:
    active = 0
    peak = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return httpx.Response(200, json=[{"rows": []}])

    async with WttClient(
        settings=_settings(max_concurrent_requests=2),
        transport=httpx.MockTransport(handler),
    ) as client:
        await asyncio.gather(
            *(client.fetch_events_by_year(year) for year in range(2021, 2027))
        )

    assert peak <= 2


async def test_retries_503_then_succeeds() -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(200, json=[{"rows": []}])

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        response = await client.fetch_events_by_year(2023)

    assert response.status_code == 200
    assert calls == 2


async def test_exhausted_retries_raise_request_error() -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": "unavailable"})

    async with WttClient(
        settings=_settings(max_retry_attempts=4),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(WttRequestError) as error:
            await client.fetch_events_by_year(2023)

    assert error.value.status_code == 503
    assert calls == 4


async def test_does_not_retry_404() -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, json={"error": "not found"})

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(WttRequestError) as error:
            await client.fetch_events_by_year(2023)

    assert error.value.status_code == 404
    assert calls == 1


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_retries_transient_server_errors(status: int) -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(status, json={"error": "transient"})
        return httpx.Response(200, json=[{"rows": []}])

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        response = await client.fetch_events_by_year(2023)

    assert response.status_code == 200
    assert calls == 2


async def test_does_not_retry_non_transient_5xx() -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(501, json={"error": "not implemented"})

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(WttRequestError) as error:
            await client.fetch_events_by_year(2023)

    assert error.value.status_code == 501
    assert calls == 1


async def test_honours_retry_after_header_on_429() -> None:
    calls = 0
    retry_after_seconds = 0.2

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                json={"error": "slow down"},
                headers={"Retry-After": str(retry_after_seconds)},
            )
        return httpx.Response(200, json=[{"rows": []}])

    started = time.monotonic()
    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        response = await client.fetch_events_by_year(2023)
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert calls == 2
    assert elapsed >= retry_after_seconds


async def test_retries_transport_errors() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("connection dropped", request=request)
        return httpx.Response(200, json=[{"rows": []}])

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        response = await client.fetch_events_by_year(2023)

    assert response.status_code == 200
    assert calls == 2


async def test_invalid_json_raises_a_payload_error() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    async with WttClient(
        settings=_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(WttPayloadError):
            await client.fetch_events_by_year(2023)

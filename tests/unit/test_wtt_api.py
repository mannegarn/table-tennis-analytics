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

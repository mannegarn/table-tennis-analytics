"""Async HTTP client for the WTT API.

The client owns three concerns that would otherwise leak into collectors:

* a semaphore, so concurrency stays bounded;
* a retry policy, so transient failures are retried but client errors are not;
* a typed result, so callers never guess what a response contained.
"""

import asyncio
import random
from collections.abc import Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from tt_stats.core.clients.wtt_routes import get_events_year_route
from tt_stats.core.config import Settings, get_settings


class WttError(RuntimeError):
    """Base class for WTT client failures."""


class WttRequestError(WttError):
    """A request failed after every retry attempt."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class WttPayloadError(WttError):
    """The response arrived but could not be parsed as JSON."""


@dataclass(frozen=True, slots=True)
class WttResponse:
    """A successful response, kept as bytes as well as parsed JSON.

    The bytes matter because the raw payload is hashed before landing, so an
    empty or changed response is visible later.
    """

    status_code: int
    content: bytes
    data: Any


def _is_retryable(error: BaseException) -> bool:
    """Return True only for transient failures worth another attempt."""
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return status == 429 or status >= 500
    return isinstance(error, httpx.RequestError)


class WttClient:
    """Bounded, retrying async client for the WTT JSON endpoints."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._semaphore = asyncio.Semaphore(self._settings.max_concurrent_requests)
        self._http = httpx.AsyncClient(
            timeout=self._settings.request_timeout_seconds,
            verify=self._settings.verify_ssl,
            transport=transport,
        )

    @property
    def max_concurrent_requests(self) -> int:
        """The configured request limit, exposed for logging and tests."""
        return self._settings.max_concurrent_requests

    async def __aenter__(self) -> "WttClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._http.aclose()

    async def fetch_events_by_year(self, year: int) -> WttResponse:
        """Fetch one year of the event calendar."""
        route = get_events_year_route(
            year,
            api_key=self._settings.wtt_api_key,
            user_agent=self._settings.user_agent,
        )
        return await self.post_json(
            url=route["url"],
            json_payload=route["json_payload"],
            headers=route["headers"],
        )

    async def post_json(
        self,
        *,
        url: str,
        json_payload: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> WttResponse:
        """POST a JSON body and return the parsed response.

        Retries transient errors with exponential backoff and jitter. Client
        errors such as 400 or 403 are raised immediately, because repeating
        them cannot change the outcome.
        """
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._settings.max_retry_attempts),
            wait=wait_exponential_jitter(
                initial=self._settings.retry_wait_min_seconds,
                max=self._settings.retry_wait_max_seconds,
                jitter=self._settings.request_jitter_seconds,
            ),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        )

        try:
            async for attempt in retrying:
                with attempt:
                    return await self._post_once(
                        url=url,
                        json_payload=json_payload,
                        headers=headers,
                    )
        except (httpx.HTTPStatusError, httpx.RequestError) as error:
            status = (
                error.response.status_code
                if isinstance(error, httpx.HTTPStatusError)
                else None
            )
            raise WttRequestError(
                f"WTT request failed: {error}", status_code=status
            ) from error

        raise WttRequestError("WTT request retry loop exited without a result")

    async def _post_once(
        self,
        *,
        url: str,
        json_payload: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> WttResponse:
        async with self._semaphore:
            if self._settings.request_jitter_seconds > 0:
                await asyncio.sleep(
                    random.uniform(0, self._settings.request_jitter_seconds)
                )

            # Let httpx set the content type for a json= body.
            clean_headers = {
                key: value
                for key, value in headers.items()
                if key.lower() != "content-type"
            }

            response = await self._http.post(
                url,
                json=json_payload,
                headers=clean_headers,
            )
            response.raise_for_status()

            try:
                data = response.json()
            except ValueError as error:
                raise WttPayloadError(
                    f"WTT response was not valid JSON: {error}"
                ) from error

            return WttResponse(
                status_code=response.status_code,
                content=response.content,
                data=data,
            )

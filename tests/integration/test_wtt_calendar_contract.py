"""Calendar contract tests: committed fixture and optional live call.

The live call is opt-in because it hits the real WTT API:
    TT_LIVE_API_TESTS=1 make test-integration
"""

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from tt_stats.core.clients.wtt_api import WttClient, WttResponse
from tt_stats.events.collect.events_by_year import DATE_FORMAT, check_payload

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "events_2023.json"


def test_committed_fixture_matches_the_contract() -> None:
    if not FIXTURE.exists():
        pytest.skip("capture the fixture with make events-collect first")

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    checked = check_payload(2023, payload)

    assert checked.error is None
    assert checked.received_count > 0


def test_live_2023_calendar_matches_the_contract() -> None:
    if os.getenv("TT_LIVE_API_TESTS") != "1":
        pytest.skip("set TT_LIVE_API_TESTS=1 to call the live WTT API")

    async def fetch() -> WttResponse:
        async with WttClient() as client:
            return await client.fetch_events_by_year(2023)

    response = asyncio.run(fetch())
    checked = check_payload(2023, response.data)

    assert response.status_code == 200
    assert checked.error is None
    assert checked.received_count > 0

    sample = response.data[0]["rows"][0]
    assert isinstance(sample["EventId"], int)
    datetime.strptime(sample["StartDateTime"], DATE_FORMAT)

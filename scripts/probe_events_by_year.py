"""Scratch probe for the WTT event calendar, one request per year.

Local testing only: not imported by the package, not shipped in the wheel, and
the captured payloads are git-ignored. Its job is to answer three questions that
the collector cannot answer on its own:

* which years does the API actually serve tournaments for?
* what does a year with nothing in it look like (``[]``, an empty ``rows``, or
  a null)?
* do ``Count`` and the parsed row count ever disagree?

Run it with::

    uv run python scripts/probe_events_by_year.py
    uv run python scripts/probe_events_by_year.py --from-year 1926 --to-year 1960
    uv run python scripts/probe_events_by_year.py --years 2023 2024 2027

A payload is written next to this script only when the year has rows, so a
hundred empty years do not leave a hundred empty files behind.
"""

import argparse
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx

_BASE_URL = "https://wtt-website-api-prod-3-frontdoor-bddnb2haduafdze9.a01.azurefd.net"
URL = f"{_BASE_URL}/api/eventcalendar"

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "origin": "https://www.worldtabletennis.com",
    "referer": "https://www.worldtabletennis.com/",
    "accept-language": "en-GB,en;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Linux; Android 11.0; Surface Duo) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Mobile Safari/537.36"
    ),
    "secapimkey": "S_WTT_882jjh7basdj91834783mds8j2jsd81",
}

#: Earliest year worth asking about. The calendar is believed to reach back to
#: the 1930s; the probe starts slightly before that so the first real year is
#: visible rather than assumed.
DEFAULT_FROM_YEAR = 1926


@dataclass(frozen=True, slots=True)
class YearProbe:
    """What one year's request produced, in the terms the collector cares about."""

    year: int
    status_code: int | None
    rows: int
    source_count: int | None
    source_original_count: int | None
    payload_bytes: int
    shape: str
    error: str | None
    saved_file: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _say(message: str) -> None:
    """Print one line. A probe is a script, not application logging."""
    print(message)  # noqa: T201


def _custom_filter(year: int) -> str:
    """Return the JSON-string filter body the endpoint expects."""
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
    return json.dumps(inner_filter)


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _post_with_retries(
    client: httpx.Client,
    year: int,
    *,
    attempts: int = 4,
) -> httpx.Response:
    """POST one year, retrying timeouts the way ``WttClient`` does.

    A single 40-second attempt is not representative: the collector retries
    transient failures, and this endpoint genuinely stalls sometimes. Without
    this the probe reports upstream flakiness as if it were a missing year.
    """
    delay = 2.0
    last_error: str = "no attempt made"

    for attempt in range(1, attempts + 1):
        try:
            return client.post(
                URL,
                json={"custom_filter": _custom_filter(year)},
                headers=HEADERS,
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            last_error = f"{type(error).__name__}: {error}"
            if attempt == attempts:
                break
            _say(f"{year} | attempt {attempt} failed ({type(error).__name__})")
            time.sleep(delay)
            delay *= 2

    raise httpx.RequestError(f"{attempts} attempts failed: {last_error}")


def _read_shape(payload: Any) -> tuple[int, int | None, int | None, str]:
    """Return ``(rows, Count, Original_Count, shape)`` without raising.

    An unexpected shape is a finding, not an exception: the probe exists to
    report what came back, so nothing here may abort the sweep.
    """
    if payload is None:
        return 0, None, None, "null"
    if not isinstance(payload, list):
        return 0, None, None, f"not-a-list:{type(payload).__name__}"
    items = cast("list[Any]", payload)
    if not items:
        return 0, None, None, "empty-list"

    first = items[0]
    if not isinstance(first, dict):
        return 0, None, None, f"list-of:{type(first).__name__}"

    envelope = cast("dict[str, Any]", first)
    raw_rows = envelope.get("rows")
    rows = len(cast("list[Any]", raw_rows)) if isinstance(raw_rows, list) else 0
    counts = (_as_int(envelope.get("Count")), _as_int(envelope.get("Original_Count")))
    shape = "rows-envelope" if isinstance(raw_rows, list) else "no-rows-key"
    return rows, counts[0], counts[1], shape


def probe_year(client: httpx.Client, year: int) -> YearProbe:
    """Request one year and describe the response."""
    try:
        response = _post_with_retries(client, year)
        response.raise_for_status()
    except httpx.HTTPError as error:
        status = getattr(getattr(error, "response", None), "status_code", None)
        return YearProbe(
            year=year,
            status_code=status,
            rows=0,
            source_count=None,
            source_original_count=None,
            payload_bytes=0,
            shape="-",
            error=f"{type(error).__name__}: {error}",
        )

    try:
        payload: Any = response.json()
    except ValueError as error:
        return YearProbe(
            year=year,
            status_code=response.status_code,
            rows=0,
            source_count=None,
            source_original_count=None,
            payload_bytes=len(response.content),
            shape="invalid-json",
            error=str(error),
        )

    rows, source_count, original_count, shape = _read_shape(payload)
    saved_file: str | None = None
    if rows > 0:
        target = Path(__file__).with_name(f"events_by_year_{year}.json")
        target.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        saved_file = target.name

    return YearProbe(
        year=year,
        status_code=response.status_code,
        rows=rows,
        source_count=source_count,
        source_original_count=original_count,
        payload_bytes=len(response.content),
        shape=shape,
        error=None,
        saved_file=saved_file,
    )


def _years_from_args(args: argparse.Namespace) -> list[int]:
    if args.years:
        return sorted(cast("list[int]", args.years))
    to_year = args.to_year or datetime.now(tz=UTC).year + 1
    if to_year < args.from_year:
        raise SystemExit(f"--to-year {to_year} is before --from-year {args.from_year}")
    return list(range(args.from_year, to_year + 1))


def main(argv: list[str] | None = None) -> int:
    """Sweep the requested years and print a one-line row per year."""
    parser = argparse.ArgumentParser(
        description="Probe the WTT event calendar year by year."
    )
    parser.add_argument(
        "--years",
        nargs="+",
        type=int,
        default=None,
        help="Explicit years to probe; overrides --from-year/--to-year.",
    )
    parser.add_argument("--from-year", type=int, default=DEFAULT_FROM_YEAR)
    parser.add_argument(
        "--to-year",
        type=int,
        default=None,
        help="Default: next calendar year, so a Q4 publication is visible.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.2,
        help="Seconds between requests. 0 disables the pause.",
    )
    args = parser.parse_args(argv)

    years = _years_from_args(args)
    _say(f"probing {len(years)} years: {years[0]}..{years[-1]}")

    results: list[YearProbe] = []
    with httpx.Client(timeout=90.0) as client:
        for index, year in enumerate(years):
            probe = probe_year(client, year)
            results.append(probe)
            if probe.error is None:
                written = f" -> {probe.saved_file}" if probe.saved_file else ""
                _say(
                    f"{year} | {probe.status_code} | rows={probe.rows:4d} | "
                    f"Count={probe.source_count} | "
                    f"Orig={probe.source_original_count} | "
                    f"shape={probe.shape} | {probe.payload_bytes} B{written}"
                )
            else:
                _say(f"{year} | ERROR | {probe.error}")
            if args.delay and index < len(years) - 1:
                time.sleep(args.delay)

    populated = [probe for probe in results if probe.rows > 0]
    failed = [probe for probe in results if not probe.ok]
    total_rows = sum(probe.rows for probe in results)
    mismatched = [
        probe
        for probe in populated
        if probe.source_count is not None and probe.source_count != probe.rows
    ]

    _say("")
    _say(f"years probed      : {len(results)}")
    _say(f"years with rows   : {len(populated)}")
    _say(f"first year with rows: {populated[0].year if populated else '-'}")
    _say(f"last year with rows : {populated[-1].year if populated else '-'}")
    _say(f"total rows        : {total_rows}")
    _say(f"failed requests   : {len(failed)}")
    if mismatched:
        _say(
            "Count != parsed rows for: "
            + ", ".join(f"{p.year} ({p.source_count} vs {p.rows})" for p in mismatched)
        )

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

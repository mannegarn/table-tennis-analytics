"""The one collector loop every WTT endpoint runs through.

An endpoint module describes only what is specific to it, as an
:class:`Endpoint`: which request to make, how to check a payload, and the words
used in logs. It also supplies its pending queue and landing function to
:func:`collector_main`. Everything else lives here once:

1. each chunk of keys is fetched concurrently (the client's semaphore, not the
   chunk, bounds requests in flight);
2. each result is settled in order: checked, summarised, and either landed at
   once (``land``) or buffered (``land_chunk``);
3. at the end of the chunk the buffer is landed in one transaction, and every
   attempt is written to ``ingest_wtt.ingest_runs``.

A failed item never stops its chunk. A failed chunk landing stops the run: its
transaction rolled back, and carrying on would only repeat the failure.
"""

from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import partial
from typing import Any

from psycopg import Connection

from tt_stats.core.batching import FetchedItem, collect_in_chunks
from tt_stats.core.cli import (
    add_queue_arguments,
    chunk_count,
    log_chunk_progress,
    progress_message,
    quiet_http_logs,
    report_collect_run,
    run_with_progress,
    validate_queue_arguments,
)
from tt_stats.core.clients.wtt_api import WttClient, WttResponse
from tt_stats.core.config import Settings, get_settings
from tt_stats.core.db import get_connection
from tt_stats.core.ingest_runs import (
    RunRecord,
    record_runs,
    safe_record_runs,
    status_for,
)
from tt_stats.core.logging_config import configure_logging, get_logger
from tt_stats.core.progress import plural

logger = get_logger(__name__)

#: Recorded as the hash of a 404 that an endpoint treats as "nothing there".
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class PayloadShapeError(ValueError):
    """The response is not the shape the endpoint documents."""


@dataclass(frozen=True, slots=True)
class Checked:
    """What an endpoint's payload check found.

    ``error`` marks the item failed, so it is recorded but never landed.
    ``payload`` replaces the response body as the thing to land, for endpoints
    that decode it first; ``None`` lands the body as received. ``facts`` are
    numbers the source reports about itself, carried onto the summary so they
    can be logged and landed.
    """

    received_count: int
    error: str | None = None
    payload: Any = None
    facts: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FetchSummary[KeyT]:
    """What happened for one key, including the evidence for a failure."""

    key: KeyT
    status_code: int | None
    #: Items parsed from the payload, by the endpoint's own definition.
    received_count: int
    payload_sha256: str | None
    fetched_at: datetime
    error: str | None
    #: Why the queue asked for this key. None when it was named explicitly.
    reason: str | None = None
    facts: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Endpoint[KeyT]:
    """Everything that differs between two collectors' fetch-and-check loops."""

    #: Prefix for log events and the progress bar, e.g. ``match_entries``.
    name: str
    #: The ``ingest_wtt.ingest_runs.endpoint`` value.
    endpoint: str
    #: One requested item, singular: ``year``, ``event``, ``match``.
    unit: str
    #: What the items contain, plural: ``events``, ``matches``, ``players``.
    found_label: str
    fetch: Callable[[WttClient, KeyT], Awaitable[WttResponse]]
    #: Check one payload. Raises ``PayloadShapeError`` for the wrong shape.
    check: Callable[[KeyT, Any], Checked]
    raw_table: str
    unpacked_table: str | None = None
    #: How a key is written in ``ingest_runs``, logs and the progress bar.
    entity_key: Callable[[KeyT], str] = str
    #: Set it to make a 404 mean "nothing there": a success that lands this
    #: payload, so the queue stops asking. ``None`` makes a 404 a failure.
    not_found_payload: Any = None


def object_rows(value: object, *, what: str) -> list[dict[str, Any]]:
    """Return ``value`` as a list of JSON objects, or raise PayloadShapeError.

    ``what`` names the rows in the error message, e.g. ``"match rows"``.
    """
    if not isinstance(value, list):
        raise PayloadShapeError(
            f"expected a list of {what}, got {type(value).__name__}"
        )
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            raise PayloadShapeError(f"row {index} is not an object")
    return value


def rows_error(problems: Sequence[str]) -> str | None:
    """Fold row-level contract problems into one error, or None when clean."""
    if not problems:
        return None
    return "row validation failed: " + "; ".join(problems[:5])


async def collect[KeyT](
    endpoint: Endpoint[KeyT],
    keys: Sequence[KeyT],
    *,
    client: WttClient,
    land: Callable[[FetchSummary[KeyT], Any], None] | None = None,
    land_chunk: (
        Callable[[Sequence[tuple[FetchSummary[KeyT], Any]]], None] | None
    ) = None,
    record: Callable[[Sequence[RunRecord]], None] | None = None,
    progress: Callable[[FetchSummary[KeyT]], None] | None = None,
    chunk_size: int | None = None,
    reasons: Mapping[KeyT, str] | None = None,
) -> list[FetchSummary[KeyT]]:
    """Fetch, check, land and record every key, one chunk at a time.

    Pass ``land`` to land each item in its own transaction as it settles: a
    landing failure then marks only that item failed. Pass ``land_chunk`` to
    land a whole chunk in one transaction at its end, which is far faster for
    many small payloads; a landing failure then stops the run.

    ``record`` receives each chunk's attempts once the chunk has landed, and
    ``progress`` each summary as it settles. ``chunk_size`` of ``None`` is one
    chunk.

    Raises:
        ValueError: If both landing styles are given, or ``chunk_size`` < 1.
    """
    if land is not None and land_chunk is not None:
        raise ValueError("pass land or land_chunk, not both")

    to_land: list[tuple[FetchSummary[KeyT], Any]] = []
    runs: list[RunRecord] = []

    def settle(fetched: FetchedItem[KeyT]) -> FetchSummary[KeyT]:
        summary, payload = _summarise(endpoint, fetched)
        if summary.error is None and land is not None:
            try:
                land(summary, payload)
            except Exception as error:
                summary = replace(summary, error=f"landing failed: {error}")
        elif summary.error is None and land_chunk is not None:
            to_land.append((summary, payload))

        _log_item(endpoint, summary)
        runs.append(_run_record(endpoint, summary, started_at=fetched.started_at))
        return summary

    def end_chunk(
        index: int,
        total: int,
        settled: list[FetchSummary[KeyT]],
        started_at: datetime,
    ) -> None:
        try:
            if land_chunk is not None and to_land:
                land_chunk(list(to_land))
        except Exception as error:
            # The transaction rolled back, so nothing in to_land was stored.
            # Say so in the run history rather than leaving them "succeeded".
            _mark_unlanded(endpoint, runs, to_land, error=error)
            logger.error(
                f"{endpoint.name}_chunk_landing_failed",
                extra={
                    "chunk": index,
                    plural(len(to_land), endpoint.unit): len(to_land),
                    "error": str(error),
                },
            )
            raise
        finally:
            # Run history is diagnostics: a failed write is logged, not raised.
            safe_record_runs(record, list(runs))
            to_land.clear()
            runs.clear()

        log_chunk_progress(
            logger,
            index,
            total,
            settled,
            started_at,
            event=f"{endpoint.name}_chunk",
            unit=endpoint.unit,
            found_label=endpoint.found_label,
        )

    return await collect_in_chunks(
        keys,
        fetch=lambda key: endpoint.fetch(client, key),
        settle=settle,
        on_chunk=end_chunk,
        progress=progress,
        chunk_size=chunk_size,
        reasons=reasons,
    )


def _summarise[KeyT](
    endpoint: Endpoint[KeyT],
    fetched: FetchedItem[KeyT],
) -> tuple[FetchSummary[KeyT], Any]:
    """Turn one fetch into a summary, plus the payload to land if it passed."""
    response = fetched.response
    digest: str | None = None
    payload: Any = None

    if response is not None:
        digest = hashlib.sha256(response.content).hexdigest()
        try:
            checked = endpoint.check(fetched.key, response.data)
        except PayloadShapeError as error:
            checked = Checked(0, error=f"payload shape error: {error}")
        payload = response.data if checked.payload is None else checked.payload
    elif fetched.status_code == 404 and endpoint.not_found_payload is not None:
        checked = Checked(0)
        digest = EMPTY_SHA256
        payload = endpoint.not_found_payload
    else:
        checked = Checked(0, error=fetched.error or "request failed")

    summary = FetchSummary(
        key=fetched.key,
        status_code=fetched.status_code,
        received_count=checked.received_count,
        payload_sha256=digest,
        fetched_at=fetched.fetched_at,
        error=checked.error,
        reason=fetched.reason,
        facts=checked.facts,
    )
    return summary, payload


def _mark_unlanded[KeyT](
    endpoint: Endpoint[KeyT],
    runs: list[RunRecord],
    unlanded: Sequence[tuple[FetchSummary[KeyT], Any]],
    *,
    error: Exception,
) -> None:
    """Rewrite, in place, the runs of items a failed chunk landing dropped."""
    keys = {endpoint.entity_key(summary.key) for summary, _ in unlanded}
    message = f"chunk landing failed: {error}"
    runs[:] = [
        replace(run, status=status_for(message), error=message)
        if run.entity_key in keys
        else run
        for run in runs
    ]


def _run_record[KeyT](
    endpoint: Endpoint[KeyT],
    summary: FetchSummary[KeyT],
    *,
    started_at: datetime,
) -> RunRecord:
    return RunRecord(
        endpoint=endpoint.endpoint,
        entity_key=endpoint.entity_key(summary.key),
        status=status_for(summary.error),
        started_at=started_at,
        http_status=summary.status_code,
        received_count=summary.received_count,
        payload_sha256=summary.payload_sha256,
        error=summary.error,
    )


def _log_item[KeyT](endpoint: Endpoint[KeyT], summary: FetchSummary[KeyT]) -> None:
    """One record per item. DEBUG unless it failed: the chunk line counts them."""
    extra: dict[str, Any] = {
        endpoint.unit: endpoint.entity_key(summary.key),
        "reason": summary.reason,
        "status": summary.status_code,
        "received_count": summary.received_count,
        **summary.facts,
    }
    if summary.error:
        logger.error(
            f"{endpoint.name}_incomplete", extra={**extra, "error": summary.error}
        )
    elif summary.received_count == 0:
        logger.debug(f"{endpoint.name}_empty", extra=extra)
    else:
        logger.debug(f"{endpoint.name}_saved", extra=extra)


#: Returns ``(key, reason)`` pairs in priority order. It receives the ids given
#: on the command line, or None; a None reason marks a key named explicitly.
QueueFn = Callable[
    [Connection[Any], Settings, list[int] | None],
    Sequence[tuple[Any, str | None]],
]


def collector_main[KeyT](
    endpoint: Endpoint[KeyT],
    argv: Sequence[str] | None,
    *,
    description: str,
    ids_flag: str,
    ids_help: str,
    queue: QueueFn,
    land: Callable[[Connection[Any], FetchSummary[KeyT], Any], None] | None = None,
    land_chunk: (
        Callable[[Connection[Any], Sequence[tuple[FetchSummary[KeyT], Any]]], None]
        | None
    ) = None,
) -> int:
    """Console entry point shared by every ``tt-*-collect`` command.

    Parses ``ids_flag`` (integers), ``--limit`` and ``--chunk-size``, builds
    the queue, runs :func:`collect` under a progress bar, and prints the tally.
    ``--limit`` takes a prefix of the queue. Returns the process exit code.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        ids_flag, dest="ids", nargs="+", type=int, default=None, help=ids_help
    )
    add_queue_arguments(parser, unit=endpoint.unit)
    args = parser.parse_args(argv)
    validate_queue_arguments(parser, args)

    configure_logging()
    settings = get_settings()
    quiet_http_logs(settings)

    chunk_size: int = args.chunk_size or settings.chunk_size
    started_at = datetime.now(tz=UTC)
    units = plural(2, endpoint.unit)

    with get_connection(autocommit=True) as connection:
        queued = list(queue(connection, settings, args.ids))[: args.limit]
        keys: list[KeyT] = [key for key, _ in queued]
        reasons: dict[KeyT, str] = {
            key: reason for key, reason in queued if reason is not None
        }
        logger.info(
            f"{endpoint.name}_queue",
            extra={
                units: len(keys),
                "reasons": dict(Counter(reason or "manual" for _, reason in queued)),
            },
        )

        if not keys:
            logger.info(f"{endpoint.name}_nothing_pending")
            print(  # noqa: T201
                f"Nothing pending: every {endpoint.unit} is already fetched."
            )
            return 0

        chunks_total = chunk_count(len(keys), chunk_size)
        logger.info(
            f"{endpoint.name}_started",
            extra={
                units: len(keys),
                "chunk_size": chunk_size,
                "chunks": chunks_total,
                "concurrency": settings.max_concurrent_requests,
            },
        )

        async def run(
            advance: Callable[[FetchSummary[KeyT]], None],
        ) -> list[FetchSummary[KeyT]]:
            async with WttClient() as client:
                return await collect(
                    endpoint,
                    keys,
                    client=client,
                    land=partial(land, connection) if land else None,
                    land_chunk=partial(land_chunk, connection) if land_chunk else None,
                    record=partial(record_runs, connection),
                    progress=advance,
                    chunk_size=chunk_size,
                    reasons=reasons or None,
                )

        results = run_with_progress(
            len(keys),
            description=f"Collecting {endpoint.name.replace('_', ' ')}",
            unit=endpoint.unit,
            chunk_size=chunk_size,
            settings=settings,
            message=lambda summary: progress_message(
                summary,
                key=f"{endpoint.unit}={endpoint.entity_key(summary.key)}",
                count_label=endpoint.found_label,
            ),
            run=run,
            reasons=reasons or None,
        )

    return report_collect_run(
        results,
        started_at=started_at,
        chunks_total=chunks_total,
        event=endpoint.name,
        unit=endpoint.unit,
        found_label=endpoint.found_label,
        raw_table=endpoint.raw_table,
        unpacked_table=endpoint.unpacked_table,
    )

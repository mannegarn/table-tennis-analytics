"""Shared plumbing for the collector and unpack console entry points.

Every endpoint ships two commands: a collector (``tt-<domain>-collect``) and a
backfill unpacker (``tt-<domain>-unpack``). Their *domain* parts differ - which
flags name the work (``--years``, ``--event-ids``), which pending view feeds the
queue, what a summary counts - but the scaffolding around them does not. That
scaffolding lives here so a new endpoint only writes what is specific to it.

Deliberately a set of small functions, not a base class or a runner that owns
``main()``: each entry point still reads top to bottom and keeps its own flags,
queue query and wording.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from tt_stats.core.logging_config import get_logger
from tt_stats.core.progress import RunTiming, plural, progress_bar

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable, Coroutine, Sequence

    from tt_stats.core.config import Settings

logger = get_logger(__name__)


class SettledSummary(Protocol):
    """What every collector summary exposes, whatever its key or extra counts."""

    @property
    def error(self) -> str | None: ...

    @property
    def received_count(self) -> int: ...

    @property
    def reason(self) -> str | None: ...


# --- argument parsing -------------------------------------------------------


def add_queue_arguments(parser: argparse.ArgumentParser, *, unit: str) -> None:
    """Add ``--limit`` and ``--chunk-size``, worded for ``unit`` (e.g. "year")."""
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=f"Fetch at most this many {unit}s from the queue, in queue order.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=None,
        help=f"{unit.capitalize()}s per batch. Defaults to Settings.chunk_size.",
    )


def validate_queue_arguments(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    """Reject non-positive ``--limit`` / ``--chunk-size`` with argparse's exit 2.

    Runs before any Settings or database work: a typo in a limit should not need
    a database password to be reported.
    """
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.chunk_size is not None and args.chunk_size < 1:
        parser.error("--chunk-size must be at least 1")


def add_dry_run_argument(parser: argparse.ArgumentParser) -> None:
    """Add the unpackers' ``--dry-run`` flag."""
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the statement, report counts, then roll back.",
    )


# --- collector run ----------------------------------------------------------


def quiet_http_logs(settings: Settings) -> None:
    """Silence per-request httpx/httpcore records unless running at DEBUG.

    One line per HTTP request would bury the audit trail.
    """
    if settings.log_level.upper() != "DEBUG":
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)


def chunk_count(items: int, chunk_size: int) -> int:
    """Number of batches ``items`` splits into at ``chunk_size``."""
    return -(-items // chunk_size)


def progress_message(summary: SettledSummary, *, key: str, count_label: str) -> str:
    """Bar postfix such as ``live event=3098 | matches=306 | ok``."""
    if summary.error:
        state = "failed"
    elif summary.received_count == 0:
        state = "empty"
    else:
        state = "ok"
    reason = f"{summary.reason} " if summary.reason else ""
    return f"{reason}{key} | {count_label}={summary.received_count} | {state}"


def run_with_progress[SummaryT](
    total: int,
    *,
    description: str,
    unit: str,
    chunk_size: int,
    settings: Settings,
    message: Callable[[SummaryT], str],
    run: Callable[[Callable[[SummaryT], None]], Coroutine[Any, Any, list[SummaryT]]],
) -> list[SummaryT]:
    """Drive an async collect under a progress bar and return its summaries.

    ``run`` receives the per-item progress callback and returns the coroutine to
    execute. The bar owns one stdout line and rewrites it; routine records are
    INFO, so the WARNING-level console handler cannot push it down a line.
    """
    with progress_bar(
        total,
        description=(
            f"{description} "
            f"({chunk_size}/chunk, {settings.max_concurrent_requests} workers)"
        ),
        unit=unit,
        disable=settings.log_json,
    ) as bar:

        def advance(summary: SummaryT) -> None:
            bar.set_postfix_str(message(summary), refresh=False)
            bar.update(1)

        return asyncio.run(run(advance))


def report_collect_run(
    results: Sequence[SettledSummary],
    *,
    started_at: datetime,
    chunks_total: int,
    event: str,
    unit: str,
    found_label: str,
) -> int:
    """Log and print the end-of-run tally, returning the process exit code.

    Args:
        results: Every settled summary from the run.
        started_at: When the run began, for elapsed time and throughput.
        chunks_total: Number of batches the run was split into.
        event: Log event prefix, e.g. ``match_entries``.
        unit: Singular noun for one requested item, e.g. ``event``.
        found_label: Plural noun for what the items contained, e.g. ``matches``.

    Returns:
        0 when every item succeeded, else 1.
    """
    timing = RunTiming(started_at, datetime.now(tz=UTC))
    failed = sum(1 for result in results if result.error)
    empty = sum(
        1 for result in results if not result.error and result.received_count == 0
    )
    found = sum(result.received_count for result in results)
    tally: dict[str, Any] = {
        f"{unit}s": len(results),
        "chunks": chunks_total,
        found_label: found,
        "failed": failed,
        "empty": empty,
        "elapsed_seconds": round(timing.elapsed.total_seconds(), 3),
        "rate_per_second": round(timing.rate_per_second(len(results)), 3),
    }
    if failed:
        logger.error(f"{event}_incomplete", extra=tally)
    else:
        logger.info(f"{event}_complete", extra=tally)

    # Deliberate stdout write: terminal logging sits at WARNING, so the record
    # above never reaches the console. This is the run summary, not a log line.
    print(  # noqa: T201
        f"{timing.describe(len(results), unit=unit)}, "
        f"{chunks_total:,} {plural(chunks_total, 'chunk')}: "
        f"{found:,} {found_label} found, "
        f"{empty:,} {plural(empty, unit)} with no {found_label}, "
        f"{failed:,} failed"
    )
    return 0 if not failed else 1

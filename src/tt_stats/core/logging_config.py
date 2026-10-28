"""Structured logging setup for the pipeline.

Two rules keep this simple:

1. **Entry points** call :func:`configure_logging` once, before doing any work.
2. **Every other module** calls :func:`get_logger` and logs.

Configuring only at the entry point is what prevents duplicate handlers and
doubled log lines later.

Output goes to stdout so whatever runs the pipeline (a shell, Docker, Airflow,
CI) captures it, and additionally to a rotating file so the record survives a
closed terminal. Both handlers share one formatter, so the file mirrors the
console format.

Extras are supported and carried through to both formats::

    logger.info("scraped_year", extra={"year": 2021, "rows": 189})
"""

import json
import logging
import sys
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from tt_stats.core.config import get_settings

# Attributes that exist on every LogRecord. Anything else found on a record is
# treated as a caller-supplied extra and carried into the output.
_RESERVED_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "stacklevel",
        "taskName",
        "thread",
        "threadName",
    }
)

_CONSOLE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s  %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_LOG_FILE_NAME = "tt_stats.log"
_DEFAULT_MAX_BYTES = 5_000_000
_DEFAULT_BACKUP_COUNT = 3


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    """Return the caller-supplied extras attached to a record."""
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _RESERVED_ATTRS and not key.startswith("_")
    }


class ConsoleFormatter(logging.Formatter):
    """Human-readable output, with extras appended as ``key=value`` pairs."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = _extras(record)
        if not extras:
            return base
        rendered = " ".join(f"{key}={value}" for key, value in extras.items())
        return f"{base}  {rendered}"


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with extras as top-level fields.

    Machine readable, so logs can be filtered, counted or alerted on rather than
    only read. ``default=str`` keeps non-serialisable values such as dates,
    Paths and Decimals from breaking the log call.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        payload.update(_extras(record))
        return json.dumps(payload, default=str)


@dataclass(frozen=True, slots=True)
class _Options:
    """Resolved handler configuration for one ``configure_logging`` call."""

    level: str
    json_output: bool
    log_to_file: bool
    log_file: Path | None
    max_bytes: int
    backup_count: int


def _resolve_options(
    level: str | None,
    json_output: bool | None,
    log_to_file: bool | None,
    log_file: Path | None,
) -> _Options:
    """Resolve level, format and file handling, reading Settings when needed.

    Passing the console options explicitly avoids touching Settings, which keeps
    the formatter tests independent of database configuration.
    """
    if level is not None and json_output is not None and log_to_file is not None:
        return _Options(
            level=level.upper(),
            json_output=json_output,
            log_to_file=log_to_file,
            log_file=log_file,
            max_bytes=_DEFAULT_MAX_BYTES,
            backup_count=_DEFAULT_BACKUP_COUNT,
        )

    settings = get_settings()
    enabled = settings.log_to_file if log_to_file is None else log_to_file
    return _Options(
        level=(level or settings.log_level).upper(),
        json_output=settings.log_json if json_output is None else json_output,
        log_to_file=enabled,
        log_file=(log_file or settings.logs_dir / _LOG_FILE_NAME) if enabled else None,
        max_bytes=settings.log_file_max_bytes,
        backup_count=settings.log_file_backup_count,
    )


def _file_handler(
    path: Path,
    *,
    max_bytes: int,
    backup_count: int,
    formatter: logging.Formatter,
) -> RotatingFileHandler:
    """Build the rotating file handler, creating its directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)
    return handler


class _BelowErrorOnConsole(logging.Filter):
    """Drop sub-error records while a progress bar is drawing on the terminal."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.ERROR


@contextmanager
def progress_owns_console() -> Generator[None]:
    """Mute the console while a progress bar owns the terminal.

    Only the console handler is gated. The file handler is untouched, so every
    record still reaches ``logs/tt_stats.log``. Errors are let through, because
    losing one of those is worse than a redrawn bar.
    """
    gated: list[tuple[logging.Handler, _BelowErrorOnConsole]] = []

    for candidate in logging.getLogger().handlers:
        # Bind to the non-generic base class: ``isinstance`` against the generic
        # ``StreamHandler``/``FileHandler`` would narrow to an unknown stream
        # type and break type checking of the collected pairs.
        handler: logging.Handler = candidate
        if isinstance(candidate, logging.FileHandler):
            # File handlers must keep every record.
            continue
        if isinstance(candidate, logging.StreamHandler):
            gate = _BelowErrorOnConsole()
            handler.addFilter(gate)
            gated.append((handler, gate))

    try:
        yield
    finally:
        for handler, gate in gated:
            handler.removeFilter(gate)


def configure_logging(
    *,
    level: str | None = None,
    json_output: bool | None = None,
    log_to_file: bool | None = None,
    log_file: Path | None = None,
) -> None:
    """Configure the root logger. Safe to call more than once.

    Defaults come from Settings, so an entry point only needs
    ``configure_logging()``.

    Existing root handlers are removed first, so repeated calls cannot result
    in duplicated output. That makes it safe to call from tests as well as from
    a process entry point.

    Args:
        level: Override the log level, for example ``"DEBUG"``.
        json_output: Override the format. ``True`` emits JSON, ``False`` emits
            readable console output.
        log_to_file: Override whether the rotating file handler is added.
        log_file: Override the file path, ignoring ``logs_dir``.
    """
    options = _resolve_options(level, json_output, log_to_file, log_file)

    formatter: logging.Formatter = (
        JsonFormatter()
        if options.json_output
        else ConsoleFormatter(_CONSOLE_FORMAT, datefmt=_DATE_FORMAT)
    )

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
        existing.close()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    if options.log_file is not None:
        root.addHandler(
            _file_handler(
                options.log_file,
                max_bytes=options.max_bytes,
                backup_count=options.backup_count,
                formatter=formatter,
            )
        )

    root.setLevel(options.level)


def get_logger(name: str) -> logging.Logger:
    """Return the logger for a module.

    Usage::

        logger = get_logger(__name__)
    """
    return logging.getLogger(name)

"""Structured logging setup for the pipeline.

Two rules keep this simple:

1. **Entry points** call :func:`configure_logging` once, before doing any work.
2. **Every other module** calls :func:`get_logger` and logs.

Configuring only at the entry point is what prevents duplicate handlers and
doubled log lines later.

Output goes to stdout, never to a file, so whatever runs the pipeline (a shell,
Docker, Airflow, CI) captures it.

Extras are supported and carried through to both formats::

    logger.info("scraped_year", extra={"year": 2021, "rows": 189})
"""

import json
import logging
import sys
from datetime import UTC, datetime
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


def _resolve_options(
    level: str | None,
    json_output: bool | None,
) -> tuple[str, bool]:
    """Resolve level and format, reading Settings only when needed.

    Passing both values explicitly avoids touching Settings at all, which keeps
    tests independent of database configuration.
    """
    if level is not None and json_output is not None:
        return level.upper(), json_output

    settings = get_settings()
    resolved_level = (level or settings.log_level).upper()
    resolved_json = settings.log_json if json_output is None else json_output
    return resolved_level, resolved_json


def configure_logging(
    *,
    level: str | None = None,
    json_output: bool | None = None,
) -> None:
    """Configure the root logger. Safe to call more than once.

    Defaults come from ``log_level`` and ``log_json`` in Settings, so an entry
    point only needs ``configure_logging()``.

    Existing root handlers are removed first, so repeated calls cannot result
    in duplicated output. That makes it safe to call from tests as well as from
    a process entry point.

    Args:
        level: Override the log level, for example ``"DEBUG"``.
        json_output: Override the format. ``True`` emits JSON, ``False`` emits
            readable console output.
    """
    resolved_level, use_json = _resolve_options(level, json_output)

    formatter: logging.Formatter = (
        JsonFormatter()
        if use_json
        else ConsoleFormatter(_CONSOLE_FORMAT, datefmt=_DATE_FORMAT)
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
        existing.close()
    root.addHandler(handler)
    root.setLevel(resolved_level)


def get_logger(name: str) -> logging.Logger:
    """Return the logger for a module.

    Usage::

        logger = get_logger(__name__)
    """
    return logging.getLogger(name)

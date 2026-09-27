"""Developer demo for the structured logging setup.

Not part of the pipeline. It exists so the logging behaviour can be inspected
in one command::

    make log-demo
    # or
    uv run tt-log-demo

Each section announces itself through the logger, so the output visibly changes
format between the console and JSON sections.
"""

from tt_stats.core.logging_config import configure_logging, get_logger

logger = get_logger("tt_stats.demo")


def main() -> int:
    """Run the demo, returning a process exit code.

    Returns:
        int: always 0. It is a demo, so there is nothing to fail.
    """
    configure_logging()
    logger.info("demo: console format, extras rendered as key=value")
    logger.info("scraped_year", extra={"year": 2021, "rows": 189})
    logger.warning("partial_event", extra={"event_id": 12345, "missing": 3})

    configure_logging(json_output=True)
    logger.info("demo: json format, extras become top-level fields")
    logger.info("scraped_year", extra={"year": 2021, "rows": 189})

    configure_logging()
    logger.debug("demo: this line is suppressed at INFO level")
    logger.info("demo: the debug line above was suppressed by the level")
    logger.error("demo: error level")

    return 0

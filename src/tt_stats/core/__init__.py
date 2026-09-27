"""Cross-cutting building blocks shared by every domain.

Nothing here knows about events, matches or players. If it is domain
specific it belongs in the corresponding domain package instead.
"""

from tt_stats.core.config import Settings, get_settings
from tt_stats.core.db import check_connection, get_connection
from tt_stats.core.logging_config import configure_logging, get_logger

__all__ = [
    "Settings",
    "check_connection",
    "configure_logging",
    "get_connection",
    "get_logger",
    "get_settings",
]

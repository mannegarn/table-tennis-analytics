"""Cross-cutting building blocks shared by every domain.

Nothing here knows about events, matches or players. If it is domain
specific it belongs in the corresponding domain package instead.
"""

from tt_stats.core.config import Settings, get_settings
from tt_stats.core.db import check_connection, get_connection

__all__ = [
    "Settings",
    "check_connection",
    "get_connection",
    "get_settings",
]

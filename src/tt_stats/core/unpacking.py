"""Run an unpack statement over many landed keys, one transaction per key.

Each endpoint's ``unpack.py`` owns its SQL, its post-unpack verification and its
preview wording. What it does not need to own is the loop around them: one
persistent ``autocommit`` connection, a ``connection.transaction()`` per key so a
failing key cannot discard the others, and a ``--dry-run`` that executes for real
and then rolls back. That loop is this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import psycopg

from tt_stats.core.db import get_connection

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from psycopg import Connection


def unpack_each[KeyT](
    keys: Sequence[KeyT] | None,
    *,
    landed_keys: Callable[[Connection[Any]], list[KeyT]],
    unpack: Callable[[Connection[Any], KeyT], int],
    preview: Callable[[Connection[Any], KeyT, int], None],
    dry_run: bool = False,
) -> list[tuple[KeyT, int]]:
    """Unpack each key in its own transaction, returning ``(key, rows)``.

    Args:
        keys: Keys to unpack. ``None`` or empty means every landed key.
        landed_keys: Returns every key present in the raw table.
        unpack: Runs and verifies the statement for one key, returning rows
            touched. Raising rolls that key back and stops the run.
        preview: Logs what a dry run would have written, called inside the
            transaction before it is rolled back.
        dry_run: Execute for real so errors and counts are genuine, then roll
            each key back instead of committing.
    """
    results: list[tuple[KeyT, int]] = []

    with get_connection(autocommit=True) as connection:
        targets = list(keys) if keys else landed_keys(connection)
        for key in targets:
            with connection.transaction():
                rows = unpack(connection, key)
                if dry_run:
                    preview(connection, key, rows)
                    # Swallowed by the transaction block, which rolls back.
                    raise psycopg.Rollback
            results.append((key, rows))

    return results

"""Load SQL statements that live in ``.sql`` files beside the code.

Unpack statements are real SQL, not string literals buried in Python: they are
read, reviewed and edited as SQL (ADR 0003). Both domains follow the same rule,
so the loader is shared: a file that contains nothing but comments means "not
written yet", and the caller treats that as a no-op instead of an error. Writing
the statement switches everything on with no code change.
"""

from __future__ import annotations

import re
from pathlib import Path

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"--[^\n]*")


def executable_sql(text: str) -> str:
    """Return ``text`` with SQL comments stripped.

    Used to tell a scaffold from a real statement. Deliberately dumb: it only
    needs to answer "is there anything here besides comments".
    """
    without_blocks = _BLOCK_COMMENT.sub(" ", text)
    return _LINE_COMMENT.sub(" ", without_blocks).strip()


def load_sql(path: Path) -> str | None:
    """Return the statement in ``path``, or ``None`` while it is a scaffold."""
    text = path.read_text(encoding="utf-8")
    if not executable_sql(text):
        return None
    return text

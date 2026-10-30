"""Test package.

Marking the test tree as a package keeps the two ``conftest`` modules
(``tests/conftest.py`` and ``tests/integration/conftest.py``) distinct module
names for mypy, which otherwise reports a duplicate-module error.
"""

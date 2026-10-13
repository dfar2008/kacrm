"""conftest for hermes tests.

Applies the same stub setup as tests/hermes_cloud/conftest.py so tests under
tests/hermes/ can import hermes.crm.db without a real PostgreSQL install.

Integration mode:
  Set ``HERMES_TESTS_REAL_PG=1`` to skip stubbing and run against a real
  PostgreSQL (requires DB up and reachable via the same DSN the app uses).
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


_REAL_PG = os.environ.get("HERMES_TESTS_REAL_PG") == "1"


def pytest_configure(config: pytest.Config) -> None:
    """Ensure project root is in sys.path so hermes.* modules are importable.

    In integration mode, the stubs are not applied so we need the real imports.
    This hook fires very early, before any fixture imports happen.
    """
    _PROJECT_ROOT = Path(__file__).resolve().parents[2]
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))


def _stub_if_missing(name: str, parent: str | None = None) -> MagicMock:
    if name in sys.modules:
        return sys.modules[name]
    mod = MagicMock(spec=types.ModuleType(name))
    sys.modules[name] = mod
    if parent and parent in sys.modules:
        setattr(sys.modules[parent], name.split(".")[-1], mod)
    return mod


if not _REAL_PG:
    for _name, _parent in [
        ("psycopg", None),
        ("psycopg.rows", "psycopg"),
        ("psycopg.connection", "psycopg"),
        ("pgvector", None),
        ("pgvector.psycopg", "pgvector"),
        ("psycopg_pool", None),
    ]:
        _stub_if_missing(_name, _parent)

    # psycopg.rows.dict_row must be callable (used as row_factory)
    sys.modules["psycopg.rows"].dict_row = MagicMock(return_value={})
    # pgvector.psycopg.register_vector must be callable (called at module init)
    sys.modules["pgvector.psycopg"].register_vector = MagicMock()

    # ConnectionPool raises on construction so _crm_reachable() returns False
    # and live-DB tests are correctly skipped in environments without a real DB.
    class _NoPoolError(RuntimeError):
        pass

    class _FakeConnectionPool:
        def __init__(self, *args, **kwargs):
            raise _NoPoolError("no real PostgreSQL in test environment — stub only")

    sys.modules["psycopg_pool"].ConnectionPool = _FakeConnectionPool

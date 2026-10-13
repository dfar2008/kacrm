"""Pure checks for alembic migration 0066 (field_config column).

Loads the migration module by file path — no DB, no alembic runner —
and asserts the revision chain + that upgrade/downgrade are callable.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "0066_module_fields_field_config.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("m0066", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_0066_file_exists():
    assert _MIGRATION.is_file(), f"missing migration: {_MIGRATION}"


def test_migration_0066_revision_chain():
    mod = _load()
    assert mod.revision == "0066"
    assert mod.down_revision == "0065"


def test_migration_0066_has_upgrade_and_downgrade():
    mod = _load()
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)

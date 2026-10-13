"""Smoke test: every real semantic YAML under
``hermes_cloud/app/dashboards/semantic/`` parses cleanly and registers
without conflict.

Tests in ``test_semantic_loader.py`` cover the loader contract on
synthetic fixtures. This file is the safety net for the *real* entity
files — adding a new entity should not crash on first dashboard hit
because of a typo, missing column, or duplicate measure name.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from hermes_cloud.app.dashboards.semantic.loader import (
    SemanticLoadError, load_entity_yaml,
)
from hermes_cloud.app.dashboards.semantic.registry import Registry


_SEMANTIC_DIR = (
    Path(__file__).resolve().parents[2]
    / "hermes_cloud" / "app" / "dashboards" / "semantic"
)


def _yaml_paths() -> list[Path]:
    return sorted(p for p in _SEMANTIC_DIR.glob("*.yml") if p.is_file())


def test_semantic_dir_has_yamls() -> None:
    paths = _yaml_paths()
    assert paths, f"no semantic YAMLs found under {_SEMANTIC_DIR}"
    # Sanity: the entities we expect to ship should each have a file.
    names = {p.stem for p in paths}
    for required in (
        "customer", "contact", "lead", "opportunity",
        # Added in the dashboards expansion (PR review punch list):
        "order", "payment", "product", "quote", "receivable",
    ):
        assert required in names, f"missing semantic YAML: {required}.yml"


@pytest.mark.parametrize("path", _yaml_paths(), ids=lambda p: p.name)
def test_each_yaml_loads(path: Path) -> None:
    """Each entity YAML must parse via :func:`load_entity_yaml`. This
    catches schema typos (unknown keys, missing required fields, wrong
    types) before the runtime hits them on a chart query."""
    e = load_entity_yaml(path)
    # Some entities use the filename as the entity key; others diverge
    # (kept for back-compat). At minimum, the loaded ``name`` should be
    # non-empty.
    assert e.name, f"{path.name} produced an empty entity name"
    assert e.source_view, f"{path.name} missing source_view"
    # Measures and dimensions are allowed to be empty (some entities are
    # detail-only), but if present each must have a name.
    for m in e.measures:
        assert m.name, f"{path.name} has a nameless measure"
    for d in e.dimensions:
        assert d.name, f"{path.name} has a nameless dimension"


def test_all_yamls_register_together_without_conflict() -> None:
    """Registering every YAML into one Registry should succeed — i.e.
    no two files claim the same entity name. ``Registry.from_directory``
    is the production code path that builds the global registry at
    process start; this test mirrors it against the real semantic dir."""
    reg = Registry.from_directory(_SEMANTIC_DIR)
    assert len(reg) == len(_yaml_paths()), (
        "duplicate entity registration — two YAMLs share the same "
        "``entity:`` key?"
    )
    # Spot-check the new entities are registered.
    for name in ("order", "payment", "product", "quote", "receivable"):
        assert reg.has_entity(name), f"{name!r} not registered"

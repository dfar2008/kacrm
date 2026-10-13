"""Regression: cloud's personality + SOUL.md reach hermes runtime.

The ``/api/user-config/personality`` and ``/api/user-config/soul`` endpoints
write to ``{profile_dir}/config.yaml`` (``agent.system_prompt``) and
``{profile_dir}/SOUL.md`` respectively.  Hermes reads both at runtime via
``hermes_cli.config.load_config()`` and ``agent.prompt_builder.load_soul_md()``
— both resolve paths through ``HERMES_HOME``.

This test proves the two sides agree: the exact files cloud writes are the
files hermes reads.  If anyone ever refactors paths on either side (e.g.
moves SOUL.md under ``memories/``), this test catches it before users see
a "prompt disappeared after restart" ticket.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.main import create_app

# ``profile_store`` is PostgreSQL-only since the InMemoryProfileStore was
# dropped (commit 367e344). The personality/SOUL.md endpoints resolve a
# profile dir via ``app.state.profile_store``, wired by ``create_app`` only
# when ``HERMES_CRM_DATABASE_URL`` reaches a real PG.
pytestmark = pytest.mark.requires_real_pg


HERMES_SRC = str((Path(__file__).resolve().parents[2] / "hermes"))


def _import_hermes():
    """Import hermes modules from the vendored submodule.

    We don't install hermes as a package in the test env, so we prepend
    ``hermes/`` to ``sys.path`` the same way the gateway subprocess does.
    """
    if HERMES_SRC not in sys.path:
        sys.path.insert(0, HERMES_SRC)
    import hermes_cli.config as hc_config  # noqa: F401
    import agent.prompt_builder as pb  # noqa: F401
    # Force re-import so module-level ``get_hermes_home()`` calls re-read env
    hc_config = importlib.reload(hc_config)
    pb = importlib.reload(pb)
    return hc_config, pb


def _make_client(tmp_path):
    app = create_app(
        AppConfig(
            api_keys={"sk-alice": "alice"},
            hermes_home_root=str(tmp_path),
        )
    )
    client = TestClient(app)
    # Trigger lifespan so default profile gets auto-created
    r = client.get(
        "/api/user-config/model",
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert r.status_code == 200
    app.state.profile_store.ensure_default_profile(user_id="alice")
    return app, client


def test_cloud_writes_are_readable_by_hermes_runtime(tmp_path, monkeypatch):
    """End-to-end: PUT personality + SOUL.md → hermes readers see both.

    This is the single regression check that binds the two sides together.
    If it fails, either cloud changed where it writes, or hermes changed
    where it reads — in either case the runtime injection breaks silently.
    """
    _, client = _make_client(tmp_path)

    # 1. Write personality + soul via the HTTP endpoints (what the UI does)
    personality_text = "You are a pirate captain named Zephyr."
    soul_text = "# SOUL\n\nI value precision and wit over politeness.\n"

    r = client.put(
        "/api/user-config/personality",
        headers={"Authorization": "Bearer sk-alice"},
        json={"system_prompt": personality_text},
    )
    assert r.status_code == 200

    r = client.put(
        "/api/user-config/soul",
        headers={"Authorization": "Bearer sk-alice"},
        json={"soul": soul_text},
    )
    assert r.status_code == 200

    # 2. Point HERMES_HOME at the profile dir — this is exactly what
    #    profile_manager does when it spawns a hermes subprocess.
    profile_dir = tmp_path / "profiles" / "alice"
    assert (profile_dir / "config.yaml").exists()
    assert (profile_dir / "SOUL.md").exists()

    monkeypatch.setenv("HERMES_HOME", str(profile_dir))

    # 3. Import hermes the way the gateway subprocess does, and read.
    hc_config, pb = _import_hermes()

    config = hc_config.load_config()
    agent_block = config.get("agent") or {}
    assert agent_block.get("system_prompt") == personality_text, (
        "hermes load_config() didn't pick up agent.system_prompt from the "
        "profile's config.yaml — cloud's personality endpoint and hermes's "
        "config loader disagree on the path or the schema."
    )

    soul = pb.load_soul_md()
    assert soul is not None
    assert "pirate" not in (soul or "").lower()  # sanity — not cross-leaked
    assert "precision and wit" in soul, (
        "hermes load_soul_md() didn't find SOUL.md where cloud wrote it. "
        "Expected <profile_dir>/SOUL.md but hermes looked elsewhere."
    )


def test_empty_soul_does_not_break_hermes_loader(tmp_path, monkeypatch):
    """SOUL.md cleared to empty → load_soul_md() returns None, not a crash."""
    _, client = _make_client(tmp_path)

    # Write then clear
    client.put(
        "/api/user-config/soul",
        headers={"Authorization": "Bearer sk-alice"},
        json={"soul": "temporary"},
    )
    client.put(
        "/api/user-config/soul",
        headers={"Authorization": "Bearer sk-alice"},
        json={"soul": ""},
    )

    profile_dir = tmp_path / "profiles" / "alice"
    monkeypatch.setenv("HERMES_HOME", str(profile_dir))

    _, pb = _import_hermes()
    assert pb.load_soul_md() is None

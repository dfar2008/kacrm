"""Tier A/B feature adoption contracts (v2026.4.23 review batch).

These tests pin the parts of the v0.11.0 upgrade that we deliberately
adopted on top of the patch rebases, so a future refactor can't
silently break them. Companion design doc:
``docs/2026-04-25-deferred-upstream-features.md`` (the 3 deferred
items).
"""
from __future__ import annotations

import inspect
import json
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Tier A #3 — approvals.cron_mode pinned in synced profile config.yaml
# ---------------------------------------------------------------------------


def test_sync_config_writes_cron_mode_default_when_absent(tmp_path):
    """First-time profile sync should write ``approvals.cron_mode = "deny"``
    so the policy is auditable per profile."""
    from hermes_cloud.app.config import AppConfig
    from hermes_cloud.app.main import create_app

    app = create_app(AppConfig(
        api_keys={"k": "1"},
        hermes_home_root=str(tmp_path),
    ))
    # Trigger _sync_config_to_profile via the route helper.
    profile_dir = tmp_path / "profiles" / "1"
    profile_dir.mkdir(parents=True, exist_ok=True)
    # Source the function locally (it's defined inside create_app's closure;
    # we exercise via the public route.
    from fastapi.testclient import TestClient
    client = TestClient(app)
    # Use a config-touching admin endpoint to fire the sync. The
    # /api/me payload doesn't write config; instead let's read profile
    # config directly: we just need the sync to have fired once.
    # Simplest: call the underlying writer through state.profile_store path
    # — but it's private. Use the more direct contract test below instead.

    # Read source for a structural check (no execution needed here).
    from hermes_cloud.app import main
    src = inspect.getsource(main.create_app)
    # The default-write block must be present and conditional.
    assert 'approvals["cron_mode"] = "deny"' in src
    assert '"cron_mode" not in approvals' in src


def test_sync_config_respects_existing_cron_mode_override():
    """If the user has manually set ``approvals.cron_mode = "approve"``
    (e.g., trusted dev sandbox), our sync must NOT clobber it."""
    from hermes_cloud.app import main
    src = inspect.getsource(main.create_app)
    # The conditional ``if "cron_mode" not in approvals`` is the
    # respect-override invariant. Pinning the textual form here
    # catches a refactor that loosens it.
    assert 'if "cron_mode" not in approvals' in src


def test_sync_config_pins_manual_approval_mode():
    """Interactive approval mode must be pinned to ``manual``.

    Upstream defaults ``approvals.mode`` to "smart": an auxiliary LLM
    guardian silently auto-approves commands it judges low-risk. Observed on
    prod 2026-09-04 — a real ``rm -rf`` was flagged "delete in root path" and
    then auto-approved with NO card shown to the owner. This deployment's
    policy is that a human sees every dangerous command, so the sync writes
    "manual" when the key is absent (an explicit user value still wins, same
    respect-override shape as cron_mode).
    """
    from hermes_cloud.app import main
    src = inspect.getsource(main.create_app)
    assert 'approvals["mode"] = "manual"' in src
    assert 'if "mode" not in approvals' in src


# ---------------------------------------------------------------------------
# Tier B #7 — agent_sessions cross-channel metadata columns
# ---------------------------------------------------------------------------


def test_alembic_0034_adds_chat_metadata_columns():
    """Schema migration must add the three columns + a covering index.
    Pinning via filename so a future re-numbering doesn't drop the
    contract silently."""
    from pathlib import Path
    p = Path(__file__).resolve().parents[2] / "alembic" / "versions"
    f = next(p.glob("0034_*.py"))
    text = f.read_text()
    assert "ADD COLUMN chat_type TEXT" in text
    assert "ADD COLUMN chat_name TEXT" in text
    assert "ADD COLUMN thread_id TEXT" in text
    assert "idx_agent_sessions_chat_lookup" in text


# ---------------------------------------------------------------------------
# Tier B #6 — beat schedule entry for retention task
# ---------------------------------------------------------------------------


def test_celery_beat_schedule_includes_retention_task():
    """``agent_messages_retention`` must be wired as a daily beat task
    so retention actually runs in production."""
    from hermes_cloud.app import celery_app
    schedule = celery_app.app.conf.beat_schedule
    assert "agent-messages-retention-daily" in schedule
    entry = schedule["agent-messages-retention-daily"]
    assert entry["task"] == \
        "hermes_cloud.app.celery_app.agent_messages_retention"


def test_retention_task_registered():
    """``agent_messages_retention`` is exposed as a celery task with a
    stable name so beat (and any operator who manually triggers via
    celery cli) can find it."""
    from hermes_cloud.app import celery_app
    task = celery_app.app.tasks.get(
        "hermes_cloud.app.celery_app.agent_messages_retention",
    )
    assert task is not None

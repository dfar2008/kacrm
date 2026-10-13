"""Module-level config resolvers in main.py — fallback policy for sync layer.

``config_store`` deliberately does NOT fall back from non-default profiles to
the user's default profile (pinned by
``test_non_default_profile_returns_none_when_unset`` in
``test_config_store_multi_profile.py``). But a per-profile hermes subprocess
needs *some* model to boot usefully — otherwise hermes calls the upstream
LLM with ``model=""`` and we get
``model: String should have at least 1 character`` (anthropic) or
``Codex Responses request 'model' must be a non-empty string`` (openai-codex).

The right layer for fallback is the sync step that materializes config into
the profile's ``config.yaml``. These tests pin the resolver helpers that
``_sync_config_to_profile`` uses.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from hermes_cloud.app.config_store import UserModelConfig, UserToolsetsConfig
from hermes_cloud.app.main import (
    _resolve_effective_model_cfg,
    _resolve_effective_toolsets_cfg,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeConfigStore:
    def __init__(self):
        # keyed by (user_id, profile_id_or_None)
        self.models: dict[tuple[str, Optional[str]], UserModelConfig] = {}
        self.toolsets: dict[tuple[str, Optional[str]], UserToolsetsConfig] = {}

    def get_user_model(self, user_id: str, profile_id: Optional[str] = None):
        # match config_store semantics: profile_id=None OR profile_id==user_id
        # is "default profile" → single logical slot keyed by user_id.
        if profile_id is None or profile_id == user_id:
            return self.models.get((user_id, None))
        return self.models.get((user_id, profile_id))

    def get_user_toolsets(self, user_id: str, profile_id: Optional[str] = None):
        if profile_id is None or profile_id == user_id:
            return self.toolsets.get((user_id, None))
        return self.toolsets.get((user_id, profile_id))


# ---------------------------------------------------------------------------
# _resolve_effective_model_cfg
# ---------------------------------------------------------------------------


def test_default_profile_uses_user_model():
    store = FakeConfigStore()
    store.models[("alice", None)] = UserModelConfig(
        user_id="alice", model="big-pickle", api_key="k", base_url="https://x",
    )
    got = _resolve_effective_model_cfg(store, user_id="alice", profile_id="alice")
    assert got is not None
    assert got.model == "big-pickle"


def test_non_default_profile_with_own_model_uses_its_own():
    store = FakeConfigStore()
    store.models[("alice", None)] = UserModelConfig(
        user_id="alice", model="default-model", api_key="k", base_url="https://x",
    )
    store.models[("alice", "research")] = UserModelConfig(
        user_id="alice", model="research-model", api_key="k2", base_url="https://y",
    )
    got = _resolve_effective_model_cfg(store, user_id="alice", profile_id="research")
    assert got is not None
    assert got.model == "research-model", "profile's own model must win"


def test_non_default_profile_without_own_model_falls_back_to_default():
    """This is the bug fix: bound feishu channel → non-default profile →
    profile_models row doesn't exist → must inherit user's default model so
    the hermes subprocess boots with a real model.
    """
    store = FakeConfigStore()
    store.models[("alice", None)] = UserModelConfig(
        user_id="alice", model="default-model", api_key="k", base_url="https://x",
    )
    # No entry for ("alice", "research")
    got = _resolve_effective_model_cfg(store, user_id="alice", profile_id="research")
    assert got is not None, "must fall back to user's default model"
    assert got.model == "default-model"


def test_non_default_profile_with_empty_model_row_falls_back():
    """A row exists but has no model / api_key / base_url → still treat as
    unconfigured and fall back."""
    store = FakeConfigStore()
    store.models[("alice", None)] = UserModelConfig(
        user_id="alice", model="default-model", api_key="k", base_url="https://x",
    )
    store.models[("alice", "research")] = UserModelConfig(
        user_id="alice", model=None, api_key=None, base_url=None,
    )
    got = _resolve_effective_model_cfg(store, user_id="alice", profile_id="research")
    assert got is not None
    assert got.model == "default-model"


def test_no_default_and_no_profile_model_returns_none():
    store = FakeConfigStore()
    got = _resolve_effective_model_cfg(store, user_id="alice", profile_id="research")
    assert got is None


def test_profile_id_none_is_default_profile():
    store = FakeConfigStore()
    store.models[("alice", None)] = UserModelConfig(
        user_id="alice", model="default-model", api_key="k", base_url="https://x",
    )
    got = _resolve_effective_model_cfg(store, user_id="alice", profile_id=None)
    assert got is not None
    assert got.model == "default-model"


# ---------------------------------------------------------------------------
# _resolve_effective_toolsets_cfg
# ---------------------------------------------------------------------------


def test_non_default_profile_without_own_toolsets_falls_back_to_default():
    store = FakeConfigStore()
    store.toolsets[("alice", None)] = UserToolsetsConfig(
        user_id="alice", toolsets=["filesystem", "web"],
    )
    got = _resolve_effective_toolsets_cfg(store, user_id="alice", profile_id="research")
    assert got is not None
    assert got.toolsets == ["filesystem", "web"]


def test_non_default_profile_with_own_toolsets_uses_its_own():
    store = FakeConfigStore()
    store.toolsets[("alice", None)] = UserToolsetsConfig(
        user_id="alice", toolsets=["filesystem"],
    )
    store.toolsets[("alice", "research")] = UserToolsetsConfig(
        user_id="alice", toolsets=["web", "gmail"],
    )
    got = _resolve_effective_toolsets_cfg(store, user_id="alice", profile_id="research")
    assert got is not None
    assert got.toolsets == ["web", "gmail"]


def test_non_default_profile_with_empty_toolsets_list_falls_back():
    store = FakeConfigStore()
    store.toolsets[("alice", None)] = UserToolsetsConfig(
        user_id="alice", toolsets=["filesystem"],
    )
    store.toolsets[("alice", "research")] = UserToolsetsConfig(
        user_id="alice", toolsets=[],
    )
    got = _resolve_effective_toolsets_cfg(store, user_id="alice", profile_id="research")
    assert got is not None
    assert got.toolsets == ["filesystem"]

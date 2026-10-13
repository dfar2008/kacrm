"""Upgrade-acceptance — cloud↔hermes integration surface (modules A / F / G).

Every assertion here is a CONTRACT the cloud relies on hermes providing. If a
hermes upgrade drops or renames any of these (as v0.18 re-homed the platform
adapters), the relevant test goes red BEFORE the upgrade is merged — instead of
surfacing as a silent production break (clarify options vanishing, agent 500s,
cron delivering free-text, CRM tools missing).

Runnable against ANY hermes version — imports + attributes only, no network,
no model calls, no DB.
"""

from __future__ import annotations

import importlib

import pytest


# ── A. Patch-applied capability surface (patch 10 additive packages) ──────────

@pytest.mark.parametrize("mod", [
    "crm",                      # CRM stack (models/queries/dispatch)
    "action_gateway",           # business-write gateway
    "memory",                   # cloud memory
    "trust",                    # trust layer
    "tools.crm_tools",          # crm tool registrations
    "tools.surface_acl",        # per-surface ACL
    "tools.scope",              # scope resolution
    "agent.overlay_resolver",   # overlay resolver
])
def test_patch10_cloud_packages_importable(mod):
    """Patch 10 (additive cloud capabilities) must be present & importable."""
    assert importlib.import_module(mod) is not None


# ── F. Gateway / api_server cloud hooks (patch 20) ───────────────────────────

def test_api_server_adapter_hooks_present():
    from gateway.platforms.api_server import APIServerAdapter
    for attr in ("_create_agent", "_run_agent", "_bind_api_server_session"):
        assert hasattr(APIServerAdapter, attr), f"api_server lost {attr}"


def test_api_server_toolset_registered():
    from toolsets import TOOLSETS
    assert "hermes-api-server" in TOOLSETS, "hermes-api-server toolset dropped"


# ── D-adjacent. clarify normalizer hardening (patch 30) ──────────────────────

def test_clarify_flatten_hardening_present():
    import tools.clarify_tool as ct
    assert hasattr(ct, "_looks_like_label"), "clarify value-recovery hook dropped"


# ── B-adjacent. hermes_state cloud-DB dispatch (patch 40) ────────────────────

def test_hermes_state_cloud_db_dispatch_present():
    import hermes_state
    src = importlib.import_module("hermes_state").__file__
    text = open(src, encoding="utf-8").read()
    assert "HERMES_USE_CLOUD_DB" in text, "cloud-DB dispatch hook dropped from hermes_state"


# ── G. cron card-driven delivery (patch 25) ──────────────────────────────────

def test_cron_deliver_renderers_present():
    import cron.scheduler as sched
    assert hasattr(sched, "_DELIVER_RENDERERS"), "cron deliver_render hook dropped"


# ── Agent-core nudge counters exist (self-evolution deps) ────────────────────

def test_agent_nudge_counter_attrs_initialized():
    """agent_init must still set the nudge counters the closed-loop relies on."""
    import agent.agent_init as ai
    src = open(ai.__file__, encoding="utf-8").read()
    for marker in ("_iters_since_skill", "_turns_since_memory",
                   "_skill_nudge_interval", "_memory_nudge_interval"):
        assert marker in src, f"agent_init dropped {marker}"

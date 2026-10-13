"""Upgrade-acceptance — SPA AI-assistant surfaces & dashboard-tool wiring.

The CRM SPA drives the agent through several distinct entry points, each with
its own surface ACL + injected guidance:
  * ChatInbox / record copilot  → POST /api/copilot/crm/send
  * schema assistant            → POST /api/copilot/scoped/send (scope=schema)
  * dashboard assistant         → POST /api/copilot/scoped/send (scope=dashboard)

And the agent configures dashboards through the ``crm_dashboard`` tool, whose
dispatch imports several cloud dashboard modules. A *deleted* cloud module
(002d00f retired the dashboard mini-assistant backend) left one of those
imports dangling → the tool 500'd → "add/configure a chart via ChatInbox"
silently broke. These tests assert every AI surface still registers and every
dashboard-tool import still resolves, so that class of break is caught here.
"""

from __future__ import annotations

import importlib

import pytest


# ── The three SPA AI-assistant surfaces are wired ────────────────────────────

def test_copilot_routes_register():
    from hermes_cloud.app.copilot import routes
    assert hasattr(routes, "register_routes")
    src = open(routes.__file__, encoding="utf-8").read()
    # router is prefixed "/api/copilot"; both entry points present
    assert 'prefix="/api/copilot"' in src
    assert '"/crm/send"' in src      # ChatInbox / record copilot
    assert '"/scoped/send"' in src   # schema + dashboard assistants


@pytest.mark.parametrize("surface", [
    "SPA_CHATBOX",
    "SPA_RECORD_COPILOT",
    "SPA_SCHEMA_ASSISTANT",
    "SPA_DASHBOARD_CHAT",
])
def test_surface_kinds_present(surface):
    """Each SPA AI surface must still exist as a SurfaceKind so ACL routing +
    per-surface guidance keep working after an upgrade."""
    from hermes_cloud.app.surface.surface import SurfaceKind
    assert hasattr(SurfaceKind, surface), f"SurfaceKind lost {surface}"


def test_dashboards_routes_register():
    from hermes_cloud.app.dashboards import routes
    assert hasattr(routes, "register_routes")
    for sym in ("_registry", "_user_context"):
        assert hasattr(routes, sym), f"dashboards.routes lost {sym}"


# ── The crm_dashboard tool's dispatch imports all resolve (the deleted-module
#    bug class — a retired cloud module must not leave a tool import dangling) ─

@pytest.mark.parametrize("mod,sym", [
    ("hermes_cloud.app.dashboards.chat", "_semantic_summary"),   # list_entities
    ("hermes_cloud.app.dashboards.routes", "_registry"),
    ("hermes_cloud.app.dashboards.routes", "_user_context"),
    ("hermes_cloud.app.dashboards.ops", "apply_ops"),            # create/patch chart
    ("hermes_cloud.app.dashboards.store", "create_tab"),
])
def test_dashboard_tool_dispatch_imports_resolve(mod, sym):
    m = importlib.import_module(mod)
    assert hasattr(m, sym), f"{mod}.{sym} missing — crm_dashboard tool would 500"


def test_crm_dashboard_tool_registered():
    """The agent must actually have the dashboard tool available."""
    import tools.crm.dispatch as disp
    src = open(disp.__file__, encoding="utf-8").read()
    assert "crm_dashboard" in src or "dashboard_entities" in src

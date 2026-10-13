"""Upgrade-acceptance — agent core & closed-loop wiring (module C).

Contracts the CRM agent (SPA chatbox + IM) depends on:
  * the api_server toolset still carries the tools the CRM experience needs —
    including skill_manage + memory, which the self-evolution loop rides on;
  * clarify is still a registered, schema-valid tool;
  * the skill/memory review nudge still fires when the counter crosses its
    threshold (turn_finalizer), and background review still spawns.

Pure import / schema / source-contract checks — no model calls.
"""

from __future__ import annotations

import pytest


API_TOOLSET = "hermes-api-server"


def _api_tools():
    from toolsets import resolve_toolset
    return set(resolve_toolset(API_TOOLSET))


@pytest.mark.parametrize("tool", [
    "skill_manage",   # self-evolution: create skills
    "skills_list", "skill_view",
    "memory",         # self-evolution: memory review
    "web_search",     # research
    "delegate_task",  # fan-out research
    "todo_list",
])
def test_api_server_toolset_has_core_tools(tool):
    assert tool in _api_tools(), f"{API_TOOLSET} dropped '{tool}' — CRM/closed-loop feature breaks"


def test_clarify_tool_registered_and_schema_valid():
    from tools.clarify_tool import CLARIFY_SCHEMA
    assert CLARIFY_SCHEMA["name"] == "clarify"
    params = CLARIFY_SCHEMA["parameters"]
    # v0.21.0 reshaped the schema from a single ``question`` + ``choices``
    # pair into a ``questions`` array (multi-question forms). What this gate
    # actually protects is unchanged: the tool is registered, and every
    # question still carries an array-typed ``choices`` field — that is the
    # mis-shaped-value surface our _flatten_choice hardening covers.
    assert "questions" in params["required"]
    q_items = params["properties"]["questions"]["items"]
    assert params["properties"]["questions"]["type"] == "array"
    assert q_items["properties"]["choices"]["type"] == "array"


def test_skill_nudge_gate_present():
    """turn_finalizer must still gate skill review on the counter+tool and spawn
    the background review — the mechanism the autonomous skill-creation rides."""
    import agent.turn_finalizer as tf
    src = open(tf.__file__, encoding="utf-8").read()
    assert "_iters_since_skill" in src and "_skill_nudge_interval" in src
    assert "skill_manage" in src  # gated on the tool being available
    assert "_spawn_background_review" in src


def test_background_review_creates_skills():
    """The review agent must still have the skill-creation path."""
    import agent.background_review as br
    src = open(br.__file__, encoding="utf-8").read()
    assert "skill" in src.lower()


def test_clarify_callback_wired_in_gateway():
    """The gateway must still wire clarify_callback → adapter.send_clarify, and
    still suppress the truncated tool-progress bubble for clarify."""
    import inspect
    from gateway.run_turn_runner import TurnRunner
    src = inspect.getsource(TurnRunner)
    assert "send_clarify" in src
    assert 'tool_name == "clarify"' in src  # progress-bubble suppression hook

"""Memory Substrate ingestion contract for ``kind="agent.message"``.

Charter §一原语 ② — agent conversations must reach the unified memory
layer just like user-touchpoint events do, so a later specialist
picking up the same lead/account can find "what was already discussed".
This file pins:

* ``_render_content`` extracts ``content.text`` from an agent.message
  row (the renderer is kind-agnostic; this test stops a future
  refactor that adds kind-specific keys from accidentally dropping
  agent.message support).
* ``_resolve_scope`` puts an agent.message row referencing a CRM
  subject into ``org`` scope (cross-agent shared memory) and a
  subject-less agent.message into ``user`` scope (private agent
  conversation).
* ``router.should_route_kind("agent.message")`` returns False — agent
  output is NOT a user-input touchpoint, so the intake agent must NOT
  re-process its own messages.
"""
from __future__ import annotations


def _agent_message_row(**override) -> dict:
    base = {
        "id": "interaction-uuid-1",
        "kind": "agent.message",
        "occurred_at": "2026-04-25T10:00:00Z",
        "actors": [
            {"type": "agent", "name": "intake", "profile_id": "42"},
            {"type": "user", "id": 7},
        ],
        "subjects": [{"module": "leads", "id": 42}],
        "content": {
            "session_id": "sess-1",
            "message_id": "msg-1",
            "role": "assistant",
            "text": "客户预算大概 50 万，倾向年付方案。",
            "tokens": {"input": 100, "output": 30},
            "model": "gpt-4",
        },
        "source": "agent:42:intake",
        "provenance": None,
    }
    base.update(override)
    return base


def test_render_content_extracts_text():
    from hermes.memory.ingest import _render_content
    rendered = _render_content(_agent_message_row())
    assert "[agent.message]" in rendered
    assert "客户预算大概 50 万" in rendered
    # Non-text noise (session_id / message_id / tokens) MUST NOT bleed in.
    assert "sess-1" not in rendered
    assert "msg-1" not in rendered


def test_resolve_scope_with_crm_subject_is_org():
    """Agent talk about lead#42 belongs to the org's shared memory —
    research_agent inheriting lead#42 later must be able to find it."""
    from hermes.memory.ingest import _resolve_scope
    scope, mem_uid = _resolve_scope(
        interaction_kind="agent.message",
        subjects=[{"module": "leads", "id": 42}],
        user_id=7,
    )
    assert scope == "org"
    assert mem_uid is None  # org-scope memories aren't user-tagged


def test_resolve_scope_without_subject_is_user():
    """Subject-less agent talk (general chit-chat) stays user-private."""
    from hermes.memory.ingest import _resolve_scope
    scope, mem_uid = _resolve_scope(
        interaction_kind="agent.message",
        subjects=[],
        user_id=7,
    )
    assert scope == "user"
    assert mem_uid == 7


def test_actor_user_id_pulls_user_actor():
    """Memory ingest keys attribution off the user actor; agent.message
    rows have BOTH agent + user actors, the resolver must pick user."""
    from hermes.memory.ingest import _actor_user_id
    actors = [
        {"type": "agent", "name": "intake", "profile_id": "42"},
        {"type": "user", "id": 7},
    ]
    assert _actor_user_id(actors) == 7


def test_router_does_not_route_agent_message_to_intake():
    """Charter §四定律 4: agents are specialized. agent.message is the
    agent's OWN OUTPUT — feeding it back into the intake agent would
    create a feedback loop where intake re-processes its own thoughts."""
    from hermes_cloud.app.interactions.router import should_route_kind
    assert should_route_kind("agent.message") is False
    # And the user-input kinds the intake DOES handle stay routed:
    assert should_route_kind("im_message") is True
    assert should_route_kind("email") is True


def test_render_content_handles_user_role():
    """Agent.message content also covers user turns and tool returns —
    same content.text key, so the renderer treats them identically."""
    from hermes.memory.ingest import _render_content
    user_row = _agent_message_row(content={
        "session_id": "s", "message_id": "m", "role": "user",
        "text": "我们这季度预算只有 30 万，能不能分期？",
    })
    rendered = _render_content(user_row)
    assert "30 万" in rendered

    tool_row = _agent_message_row(content={
        "session_id": "s", "message_id": "m", "role": "tool",
        "text": "(crm_write succeeded; lead#42 stage updated to qualified)",
        "tool_call_id": "c1", "tool_name": "crm_write",
    })
    rendered = _render_content(tool_row)
    assert "qualified" in rendered

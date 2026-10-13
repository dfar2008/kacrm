"""``emit(kind="agent.message", ...)`` contract — Charter §一定律 1 + 原语 ①.

Agent-with-user conversations are events, not "hermes internal data". The
emit() validator is the gatekeeper at the canonical Interaction Log write
path; a malformed agent.message row that drops session_id / message_id /
role can't be re-stitched into a conversation later, so we reject it
up-front rather than letting half-events into the Log.

See docs/2026-04-25-agent-message-as-event.md for the full schema this
test pins.
"""
from __future__ import annotations

import pytest

from hermes_cloud.app.interactions.emitter import _validate_agent_message


def _ok_content(**override):
    base = {
        "session_id": "sess-1",
        "message_id": "msg-1",
        "role": "assistant",
        "text": "hello",
    }
    base.update(override)
    return base


def _ok_actors(**override):
    actor = {"type": "agent", "name": "intake", "profile_id": "42"}
    actor.update(override)
    return [actor, {"type": "user", "id": 7}]


def test_happy_path_assistant_text():
    # Should not raise.
    _validate_agent_message(actors=_ok_actors(), content=_ok_content())


def test_happy_path_user_turn():
    _validate_agent_message(
        actors=_ok_actors(),
        content=_ok_content(role="user", text="my question"),
    )


def test_happy_path_tool_result_with_full_metadata():
    # tool_call_id + tool_name both present is always valid.
    _validate_agent_message(
        actors=_ok_actors(),
        content=_ok_content(
            role="tool",
            text="(tool result)",
            tool_call_id="call_abc",
            tool_name="crm_write",
        ),
    )


def test_rejects_missing_session_id():
    with pytest.raises(ValueError, match="session_id"):
        _validate_agent_message(
            actors=_ok_actors(),
            content=_ok_content(session_id=""),
        )


def test_rejects_missing_message_id():
    with pytest.raises(ValueError, match="message_id"):
        _validate_agent_message(
            actors=_ok_actors(),
            content=_ok_content(message_id=None),
        )


def test_rejects_unknown_role():
    with pytest.raises(ValueError, match="role"):
        _validate_agent_message(
            actors=_ok_actors(),
            content=_ok_content(role="bystander"),
        )


def test_rejects_empty_text():
    with pytest.raises(ValueError, match="text"):
        _validate_agent_message(
            actors=_ok_actors(),
            content=_ok_content(text=""),
        )


def test_rejects_non_dict_content():
    with pytest.raises(ValueError, match="content dict"):
        _validate_agent_message(actors=_ok_actors(), content=None)
    with pytest.raises(ValueError, match="content dict"):
        _validate_agent_message(actors=_ok_actors(), content="not a dict")  # type: ignore[arg-type]


def test_rejects_missing_agent_actor():
    """Without an agent actor, RL training can't key on which specialist
    produced the message — this is the moat data, refuse to accept anonymous."""
    with pytest.raises(ValueError, match="agent"):
        _validate_agent_message(
            actors=[{"type": "user", "id": 7}],  # only user, no agent
            content=_ok_content(),
        )


def test_rejects_agent_actor_without_name():
    with pytest.raises(ValueError, match="agent"):
        _validate_agent_message(
            actors=[{"type": "agent", "name": ""}],
            content=_ok_content(),
        )


def test_tool_role_rejects_missing_tool_call_id():
    with pytest.raises(ValueError, match="tool_call_id"):
        _validate_agent_message(
            actors=_ok_actors(),
            content=_ok_content(
                role="tool",
                text="(result)",
                tool_name="crm_write",
                # missing tool_call_id
            ),
        )


def test_tool_role_allows_missing_tool_name():
    """A tool-result message carries tool_call_id but, by message-format
    convention, not tool_name (the name is on the paired assistant
    tool_call row). The validator must accept it — requiring tool_name
    here rejected every real tool result."""
    _validate_agent_message(
        actors=_ok_actors(),
        content=_ok_content(
            role="tool",
            text="(result)",
            tool_call_id="call_abc",
            # no tool_name — still valid
        ),
    )


def test_emit_rejects_invalid_agent_message():
    """End-to-end: emit() itself must raise — the validator wiring matters
    as much as the validator content. Pin via emit() not just _validate."""
    from hermes_cloud.app.interactions import emitter

    with pytest.raises(ValueError, match="agent.message"):
        emitter.emit(
            kind="agent.message",
            actors=_ok_actors(),
            content={"role": "assistant"},  # missing session_id/message_id/text
            db=None,
        )

"""The ``memory.write`` executor rejects raw serialized payloads.

A distilled memory fact is always prose. When a specialist (e.g. the
research agent) accidentally passes its whole agent-run object
(``{'final_response': None, 'messages': [...]}``) as ``content``, the
gateway boundary must reject it so the customer-facing "facts" panel
never shows raw JSON / Python reprs.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_rejects_json_object_content():
    from hermes.action_gateway.executors.memory_write import (
        _is_serialized_payload,
    )
    assert _is_serialized_payload('{"final_response": null, "messages": []}')


def test_rejects_python_repr_dict_content():
    from hermes.action_gateway.executors.memory_write import (
        _is_serialized_payload,
    )
    assert _is_serialized_payload(
        "{'final_response': None, 'messages': [{'role': 'user'}]}"
    )


def test_rejects_json_array_content():
    from hermes.action_gateway.executors.memory_write import (
        _is_serialized_payload,
    )
    assert _is_serialized_payload('[{"role": "user", "content": "hi"}]')


def test_accepts_prose_fact():
    from hermes.action_gateway.executors.memory_write import (
        _is_serialized_payload,
    )
    assert not _is_serialized_payload(
        "蚂蚁集团整改基本完成于2023年，当前进入常态化监管阶段。"
    )


def test_accepts_prose_with_incidental_braces():
    from hermes.action_gateway.executors.memory_write import (
        _is_serialized_payload,
    )
    assert not _is_serialized_payload("客户预算约 {50万}，本周待确认")


def test_accepts_bare_number():
    from hermes.action_gateway.executors.memory_write import (
        _is_serialized_payload,
    )
    # json.loads succeeds but yields a scalar, not a dict/list — a fact.
    assert not _is_serialized_payload("42")


def test_execute_raises_on_serialized_content():
    from hermes.action_gateway.executors.memory_write import execute
    action = {
        "id": "a1",
        "params": {
            "content": '{"final_response": null, "messages": []}',
            "source_agent": "research",
        },
    }
    with pytest.raises(ValueError):
        execute(action, MagicMock())

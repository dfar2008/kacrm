"""Test self_only visibility enforcement."""
from hermes_cloud.app.scope.interaction_visibility import (
    respect_interaction_visibility,
)


def _row(actor_id, visibility=None, kind="copilot_message"):
    return {
        "id": "uuid",
        "kind": kind,
        "actors": [{"type": "user", "id": actor_id}],
        "provenance": {"visibility": visibility} if visibility else {},
        "content": {},
    }


def test_no_visibility_field_passes_through():
    rows = [_row(1)]
    assert respect_interaction_visibility(rows, caller_id=2) == rows


def test_self_only_visible_to_actor():
    row = _row(1, visibility="self_only")
    assert respect_interaction_visibility([row], caller_id=1) == [row]


def test_self_only_hidden_from_other():
    row = _row(1, visibility="self_only")
    assert respect_interaction_visibility([row], caller_id=2) == []


def test_admin_bypass_flag():
    row = _row(1, visibility="self_only")
    assert respect_interaction_visibility(
        [row], caller_id=2, admin_forensic=True
    ) == [row]


def test_mixed_rows():
    rows = [
        _row(1, visibility="self_only"),
        _row(2),  # 无 visibility 字段，对所有人可见
        _row(3, visibility="self_only"),
    ]
    out = respect_interaction_visibility(rows, caller_id=2)
    # actor=1 self_only 隐藏；actor=2 公共可见；actor=3 self_only 隐藏
    assert len(out) == 1
    assert out[0]["actors"][0]["id"] == 2

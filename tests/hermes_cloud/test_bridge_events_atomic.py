"""emit_business_write 的 interaction 发射失败必须传播（同事务原子）。"""
from __future__ import annotations
from unittest.mock import MagicMock
import pytest


def test_interaction_failure_propagates(monkeypatch):
    from hermes_cloud.app.bridge import events

    # changelog 写成功；interaction 发射抛错——必须冒泡，不被吞。
    monkeypatch.setattr(events, "ChangelogDb", lambda db: MagicMock())

    def _boom(**kwargs):
        raise RuntimeError("interaction store down")

    monkeypatch.setattr(events, "emit_interaction", _boom)

    with pytest.raises(RuntimeError, match="interaction store down"):
        events.emit_business_write(
            db=MagicMock(),
            module_id=1, module_name="accounts", record_id=7,
            op="create", old={}, new={"accounts_name": "Acme"},
            user_id=2, source="manual", field_catalog=[],
        )

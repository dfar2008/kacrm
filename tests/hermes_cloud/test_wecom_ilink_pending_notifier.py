"""WeCom iLink pending-approval notifier — Charter §1 渠道闭环 delivery.

Covers:

* ``hermes_cloud.app.channels.wecom`` registers a notifier on the
  ``wecom_ilink`` channel at import time.
* The notifier reads ``origin["external_user_id"]`` (wxid) plus the
  action's ``profile_id`` and POSTs a text approval prompt via the
  cloud's ``/internal/channel/push`` endpoint with platform=weixin.
* Missing wxid or profile_id → log + bail. The action stays approvable
  via the WebUI dashboard, so nothing is dropped.
* The approval-text builder includes a typeable prefix the operator
  can quote back as ``通过 <prefix>`` / ``拒绝 <prefix>``.
"""
from __future__ import annotations

from typing import Any, Dict, List


def test_wecom_ilink_notifier_registered_at_import():
    from hermes_cloud.app.channels import wecom  # noqa: F401
    from hermes.action_gateway import notifiers
    assert notifiers.get("wecom_ilink") is not None


def test_build_gateway_approval_text_contains_prefix_and_kind():
    from hermes_cloud.app.channels.wecom import build_gateway_approval_text
    text = build_gateway_approval_text({
        "id": "abcd1234-5678-90ab-cdef-1234567890ab",
        "kind": "crm.write",
        "agent_name": "intake",
        "summary": "create lead Acme",
    })
    assert "审批" in text
    assert "crm.write" in text
    assert "intake" in text
    assert "Acme" in text
    # Prefix is the first 8 chars — typeable, unambiguous.
    assert "abcd1234" in text
    assert "通过 abcd1234" in text
    assert "拒绝 abcd1234" in text


def test_wecom_ilink_notifier_posts_text_via_cloud(monkeypatch):
    from hermes_cloud.app.channels import wecom  # noqa: F401
    from hermes.action_gateway import notifiers

    captured: List[Dict[str, Any]] = []

    def _fake_post(*, user_id, profile_id, platform, channel_id, text):
        captured.append({
            "user_id": user_id, "profile_id": profile_id,
            "platform": platform, "channel_id": channel_id, "text": text,
        })
        return True

    monkeypatch.setattr(
        "hermes_cloud.app.insights.notify._post_via_cloud", _fake_post,
    )

    notifiers.notify_pending(
        {"id": "deadbeef-0000-0000-0000-000000000001",
         "kind": "crm.write", "agent_name": "intake",
         "summary": "create lead", "profile_id": "42"},
        {"channel": "wecom_ilink", "external_user_id": "wxid_test"},
    )
    assert len(captured) == 1
    c = captured[0]
    assert c["channel_id"] == "wxid_test"
    assert c["profile_id"] == "42"
    assert c["platform"] == "weixin"
    assert "deadbeef" in c["text"]


def test_wecom_ilink_notifier_skips_without_wxid(monkeypatch, caplog):
    import logging
    from hermes_cloud.app.channels import wecom  # noqa: F401
    from hermes.action_gateway import notifiers

    posted: List[Any] = []
    monkeypatch.setattr(
        "hermes_cloud.app.insights.notify._post_via_cloud",
        lambda **kw: posted.append(kw) or True,
    )
    caplog.set_level(logging.WARNING, logger="hermes_cloud.app.channels.wecom")

    notifiers.notify_pending(
        {"id": "x", "profile_id": "42"},
        {"channel": "wecom_ilink"},  # no external_user_id
    )
    assert posted == []
    assert any(
        "missing external_user_id" in r.getMessage() for r in caplog.records
    )


def test_wecom_ilink_notifier_skips_without_profile(monkeypatch, caplog):
    import logging
    from hermes_cloud.app.channels import wecom  # noqa: F401
    from hermes.action_gateway import notifiers

    posted: List[Any] = []
    monkeypatch.setattr(
        "hermes_cloud.app.insights.notify._post_via_cloud",
        lambda **kw: posted.append(kw) or True,
    )
    caplog.set_level(logging.WARNING, logger="hermes_cloud.app.channels.wecom")

    notifiers.notify_pending(
        {"id": "x"},  # no profile_id
        {"channel": "wecom_ilink", "external_user_id": "wxid_test"},
    )
    assert posted == []
    assert any(
        "missing profile_id" in r.getMessage() for r in caplog.records
    )

"""Upgrade-acceptance — IM adapter capability parity (module E).

v0.18 (#41112) re-homed feishu/email/dingtalk/wecom adapters from
gateway/platforms/*.py into plugins/platforms/<p>/adapter.py. Re-homing is
exactly when a required method (send_clarify, send, send_typing) or a cloud
hook silently gets left behind — and the break only shows up when a real user
on that channel hits it. These tests assert every adapter the cloud ships still
imports and still exposes the outbound contract, so a future re-home can't drop
one unnoticed.
"""

from __future__ import annotations

import importlib
import inspect

import pytest

# (import_path, class_name) for every channel the cloud drives.
_ADAPTERS = [
    ("plugins.platforms.feishu.adapter", "FeishuAdapter"),
    ("plugins.platforms.dingtalk.adapter", "DingTalkAdapter"),
    ("plugins.platforms.wecom.adapter", "WecomAdapter"),
    ("plugins.platforms.email.adapter", "EmailAdapter"),
    ("plugins.platforms.telegram.adapter", "TelegramAdapter"),
    ("plugins.platforms.discord.adapter", "DiscordAdapter"),
    ("gateway.platforms.weixin", "WeixinAdapter"),
]


def _load(import_path: str, class_name: str):
    mod = importlib.import_module(import_path)
    # Tolerate minor class-name drift: fall back to the first BasePlatformAdapter
    # subclass defined in the module.
    cls = getattr(mod, class_name, None)
    if cls is None:
        from gateway.platforms.base import BasePlatformAdapter
        for _n, _o in inspect.getmembers(mod, inspect.isclass):
            if issubclass(_o, BasePlatformAdapter) and _o is not BasePlatformAdapter:
                cls = _o
                break
    return cls


@pytest.mark.parametrize("import_path,class_name", _ADAPTERS)
def test_adapter_importable(import_path, class_name):
    assert importlib.import_module(import_path) is not None


@pytest.mark.parametrize("import_path,class_name", _ADAPTERS)
def test_adapter_outbound_contract(import_path, class_name):
    """Each adapter must expose the outbound methods the gateway calls —
    own or inherited from BasePlatformAdapter."""
    cls = _load(import_path, class_name)
    assert cls is not None, f"{import_path}: no adapter class found"
    for method in ("send", "send_clarify", "send_typing"):
        assert callable(getattr(cls, method, None)), (
            f"{cls.__name__} lost outbound method {method}()"
        )


def test_base_send_clarify_renders_choices_as_text():
    """Adapters without a native button UI (Feishu, Weixin, …) fall back to the
    base numbered-list rendering — the choices MUST appear, not vanish."""
    from gateway.platforms.base import BasePlatformAdapter
    src = inspect.getsource(BasePlatformAdapter.send_clarify)
    # numbered text fallback + awaiting-text capture must both be intact
    assert "mark_awaiting_text" in src
    assert "enumerate(choices" in src or "for i, choice" in src

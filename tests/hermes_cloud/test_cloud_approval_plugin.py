"""``cloud-approval`` plugin (hermes-patches/11) — Charter §一原语 ⑤.

The plugin lives in the hermes submodule so we load it via importlib
rather than a normal import (the submodule is on ``sys.path`` via the
patch-applied state, but the plugin path uses a hyphen which Python
disallows in module names).

These tests pin:

* The plugin module exposes ``register(ctx)`` and the two handlers.
* ``register(ctx)`` calls ``ctx.register_command`` for both
  ``approve`` + ``reject`` with ``args_hint`` set so Discord shows a
  native argument picker.
* Handlers degrade gracefully when ``HERMES_CLOUD_INTERNAL_KEY`` is
  unset (CLI / test environments).
* Handlers parse argv correctly: ``--help`` / empty / action_id /
  action_id+reason cases.
* HTTP failure paths (404 / 409 / 400) map to friendly chat strings.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _load_plugin():
    """Load the cloud-approval plugin module from the hermes submodule
    (path uses a hyphen, can't normal-import)."""
    repo_root = Path(__file__).resolve().parents[2]
    plugin_init = repo_root / "hermes" / "plugins" / "cloud-approval" / "__init__.py"
    if not plugin_init.exists():
        pytest.skip(
            "hermes-patches/11 not applied — plugin source missing. "
            "Run: bash scripts/regen-hermes-patches.sh && "
            "git apply hermes-patches/11-cloud-approval-slash-plugin.patch"
        )
    spec = importlib.util.spec_from_file_location(
        "cloud_approval_plugin", plugin_init,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Shape: plugin entry points
# ---------------------------------------------------------------------------


def test_plugin_exposes_register_and_handlers():
    mod = _load_plugin()
    assert callable(getattr(mod, "register", None))
    assert callable(getattr(mod, "_handle_approve", None))
    assert callable(getattr(mod, "_handle_reject", None))


def test_register_wires_two_slash_commands_with_args_hint():
    """``register(ctx)`` must register both /approve and /reject with
    ``args_hint`` set so Discord renders native argument pickers."""
    mod = _load_plugin()
    ctx = MagicMock()
    mod.register(ctx)
    calls = ctx.register_command.call_args_list
    assert len(calls) == 2
    names = sorted(c.args[0] for c in calls)
    assert names == ["approve", "reject"]
    for c in calls:
        assert "handler" in c.kwargs
        assert "args_hint" in c.kwargs
        assert c.kwargs["args_hint"]   # non-empty


# ---------------------------------------------------------------------------
# Argv parsing
# ---------------------------------------------------------------------------


def test_approve_help_returns_usage(monkeypatch):
    monkeypatch.delenv("HERMES_CLOUD_INTERNAL_KEY", raising=False)
    mod = _load_plugin()
    out = mod._handle_approve("--help")
    assert "用法" in out and "approve" in out
    assert "action_id" in out


def test_approve_empty_returns_usage_short_form(monkeypatch):
    monkeypatch.delenv("HERMES_CLOUD_INTERNAL_KEY", raising=False)
    mod = _load_plugin()
    out = mod._handle_approve("")
    assert "用法" in out


def test_reject_help_returns_usage(monkeypatch):
    monkeypatch.delenv("HERMES_CLOUD_INTERNAL_KEY", raising=False)
    mod = _load_plugin()
    out = mod._handle_reject("help")
    assert "用法" in out and "reject" in out


def test_reject_parses_reason_after_action_id(monkeypatch):
    """``/reject abc12345 stale customer info`` should split into
    action_id="abc12345" + reason="stale customer info" (whole tail)."""
    monkeypatch.setenv("HERMES_CLOUD_INTERNAL_KEY", "k")
    monkeypatch.delenv("HERMES_CLOUD_PROFILE_ID", raising=False)
    mod = _load_plugin()
    sent: dict = {}

    def _fake_post(action_id, decision, reason=""):
        sent["action_id"] = action_id
        sent["decision"] = decision
        sent["reason"] = reason
        return "ok"

    monkeypatch.setattr(mod, "_post_to_cloud", _fake_post)
    mod._handle_reject("abc12345 stale customer info")
    assert sent == {
        "action_id": "abc12345",
        "decision": "reject",
        "reason": "stale customer info",
    }


# ---------------------------------------------------------------------------
# Auth / connectivity guards
# ---------------------------------------------------------------------------


def test_handler_no_internal_key_returns_friendly_error(monkeypatch):
    monkeypatch.delenv("HERMES_CLOUD_INTERNAL_KEY", raising=False)
    mod = _load_plugin()
    out = mod._handle_approve("abc12345")
    # Localized, actionable error — operator sees what's wrong without
    # needing to read the plugin source.
    assert "HERMES_CLOUD_INTERNAL_KEY" in out
    assert "❌" in out


# ---------------------------------------------------------------------------
# HTTP error mapping
# ---------------------------------------------------------------------------


def _patch_urlopen_to_raise_http_error(monkeypatch, mod, *, code, body):
    """Patch the plugin's urllib.request.urlopen to raise an
    HTTPError with the given ``code`` + ``body`` JSON string."""
    import urllib.error
    import io

    class _Resp:
        def __init__(self, code, body):
            self.code = code
            self._body = body.encode("utf-8") if isinstance(body, str) else body
            self.headers = MagicMock()
            self.headers.get_content_charset.return_value = "utf-8"

        def read(self):
            return self._body

    err = urllib.error.HTTPError(
        url="http://test/",
        code=code,
        msg="test error",
        hdrs=MagicMock(),
        fp=io.BytesIO(body.encode("utf-8") if isinstance(body, str) else body),
    )
    # Patch urlopen at the urllib module level. The plugin imports
    # urllib.request locally inside _post_to_cloud, so monkeypatching
    # urllib.request.urlopen is reliable.
    import urllib.request
    monkeypatch.setattr(
        urllib.request, "urlopen",
        MagicMock(side_effect=err),
    )


def test_post_409_wrong_channel_returns_localized_message(monkeypatch):
    monkeypatch.setenv("HERMES_CLOUD_INTERNAL_KEY", "k")
    mod = _load_plugin()
    body = '{"error":{"code":"wrong_channel","message":"feishu only"}}'
    _patch_urlopen_to_raise_http_error(monkeypatch, mod, code=409, body=body)
    out = mod._handle_approve("abc12345")
    assert "原渠道处理" in out
    assert "🔒" in out


def test_post_404_returns_expired_message(monkeypatch):
    monkeypatch.setenv("HERMES_CLOUD_INTERNAL_KEY", "k")
    mod = _load_plugin()
    body = '{"error":{"code":"not_found","message":"action gone"}}'
    _patch_urlopen_to_raise_http_error(monkeypatch, mod, code=404, body=body)
    out = mod._handle_approve("abc12345")
    assert "不存在" in out or "过期" in out


def test_post_400_surfaces_message(monkeypatch):
    monkeypatch.setenv("HERMES_CLOUD_INTERNAL_KEY", "k")
    mod = _load_plugin()
    body = '{"error":{"code":"invalid","message":"already approved"}}'
    _patch_urlopen_to_raise_http_error(monkeypatch, mod, code=400, body=body)
    out = mod._handle_approve("abc12345")
    assert "already approved" in out


def test_post_success_returns_localized_confirmation(monkeypatch):
    monkeypatch.setenv("HERMES_CLOUD_INTERNAL_KEY", "k")
    monkeypatch.setenv("HERMES_CLOUD_PROFILE_ID", "42")
    mod = _load_plugin()

    import io
    class _OkResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return b'{"data": {"id": "abc12345-uuid", "state": "executed"}}'

    import urllib.request
    monkeypatch.setattr(
        urllib.request, "urlopen",
        MagicMock(return_value=_OkResp()),
    )
    out = mod._handle_approve("abc12345-uuid")
    assert "已通过" in out
    # The 8-char prefix should appear in the confirmation for operator
    # cross-reference.
    assert "abc12345" in out


# ---------------------------------------------------------------------------
# Wiring at the cloud side: ProfileManager enables the plugin
# ---------------------------------------------------------------------------


def test_profile_config_sync_enables_cloud_approval_plugin():
    """``_sync_config_to_profile`` must add ``cloud-approval`` to the
    ``plugins.enabled`` list. Pinned via inspect because the function
    is closure-defined inside ``create_app`` — we can't call it cleanly
    in isolation but we can verify the source contains the wiring."""
    import inspect
    from hermes_cloud.app import main
    src = inspect.getsource(main.create_app)
    assert '"cloud-approval"' in src
    # Idempotent append: must check ``not in enabled`` before adding.
    assert '"cloud-approval" not in enabled' in src


def test_profile_manager_forwards_internal_url_env():
    """``HERMES_CLOUD_INTERNAL_URL`` must be set on the subprocess env
    so the plugin can call back into cloud's gateway API."""
    import inspect
    from hermes_cloud.app.profile_manager import ProfileManager
    src = inspect.getsource(ProfileManager._launch_process)
    assert "HERMES_CLOUD_INTERNAL_URL" in src

"""Pure-function tests for :mod:`hermes_cloud.app.bridge.context`.

No DB required — these tests cover ``_resolve_source`` which is pure Python.
The DB-backed tests in ``test_bridge_context.py`` are skipped whenever the
database is unreachable, making this the only always-runnable coverage for
the X-Source header resolution logic.
"""
from __future__ import annotations

from unittest.mock import MagicMock


def _make_request(source_header: str | None) -> MagicMock:
    """Build a minimal mock Request with a headers dict."""
    req = MagicMock()
    if source_header is None:
        req.headers.get = lambda key, default="": default
    else:
        req.headers.get = lambda key, default="": source_header if key == "X-Source" else default
    return req


# ---------------------------------------------------------------------------
# _resolve_source
# ---------------------------------------------------------------------------


def test_resolve_source_no_header_returns_manual():
    from hermes_cloud.app.bridge.context import _resolve_source

    req = _make_request(None)
    assert _resolve_source(req) == "manual"


def test_resolve_source_empty_header_returns_manual():
    from hermes_cloud.app.bridge.context import _resolve_source

    req = _make_request("")
    assert _resolve_source(req) == "manual"


def test_resolve_source_whitespace_only_returns_manual():
    from hermes_cloud.app.bridge.context import _resolve_source

    req = _make_request("   ")
    assert _resolve_source(req) == "manual"


def test_resolve_source_bare_manual_accepted():
    from hermes_cloud.app.bridge.context import _resolve_source

    req = _make_request("manual")
    assert _resolve_source(req) == "manual"


def test_resolve_source_agent_prefix_passthrough():
    from hermes_cloud.app.bridge.context import _resolve_source

    req = _make_request("agent:profile1/extractor")
    assert _resolve_source(req) == "agent:profile1/extractor"


def test_resolve_source_feishu_prefix_passthrough():
    from hermes_cloud.app.bridge.context import _resolve_source

    req = _make_request("feishu:msg-abc-123")
    assert _resolve_source(req) == "feishu:msg-abc-123"


def test_resolve_source_system_prefix_passthrough():
    from hermes_cloud.app.bridge.context import _resolve_source

    req = _make_request("system:sync")
    assert _resolve_source(req) == "system:sync"


def test_resolve_source_unknown_prefix_returns_manual():
    from hermes_cloud.app.bridge.context import _resolve_source

    req = _make_request("malicious:payload")
    assert _resolve_source(req) == "manual"


def test_resolve_source_unknown_word_returns_manual():
    from hermes_cloud.app.bridge.context import _resolve_source

    req = _make_request("webhook")
    assert _resolve_source(req) == "manual"


def test_resolve_source_case_insensitive_match_preserves_raw():
    """Check is case-insensitive but the raw value is returned unchanged."""
    from hermes_cloud.app.bridge.context import _resolve_source

    req = _make_request("AGENT:foo")
    result = _resolve_source(req)
    assert result == "AGENT:foo"


def test_resolve_source_mixed_case_feishu():
    from hermes_cloud.app.bridge.context import _resolve_source

    req = _make_request("Feishu:ABC")
    assert _resolve_source(req) == "Feishu:ABC"

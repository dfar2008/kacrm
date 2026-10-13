"""Regression tests for ``run_utility_skill`` strict mode.

Charter v2 §一系统: a downstream LLM / transport failure must NOT be
silently converted into a 200 OK + null reply by the
``/internal/utility/run-skill`` endpoint. We learned the hard way: an
invalid OPENCODE_ZEN_API_KEY caused every cron call to come back as
"agent decided 0 cards", which made it look like the skill was
working when it had never actually run a single inference.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermes_cloud.app.utility_via_skill import (
    UtilitySkillError,
    run_utility_skill,
)


class _DispatchResult(SimpleNamespace):
    """Mimic the dispatch_to_hermes return shape just enough."""


@pytest.mark.asyncio
async def test_strict_raises_on_non_2xx(monkeypatch):
    """503 from hermes subprocess must surface as UtilitySkillError —
    not a None return that looks identical to "0 results"."""
    async def _fake_dispatch(*, app, envelope, bearer_key, timeout_sec):
        return _DispatchResult(
            status_code=503,
            raw_body='{"error":{"message":"hermes transport: ","code":"process_unavailable"}}',
            reply_text="",
        )

    monkeypatch.setattr(
        "hermes_cloud.app.proxy.dispatch.dispatch_to_hermes",
        _fake_dispatch,
    )

    fake_app = SimpleNamespace(state=SimpleNamespace(
        config=SimpleNamespace(internal_key="dev-internal-key"),
    ))

    with pytest.raises(UtilitySkillError) as ei:
        await run_utility_skill(
            app=fake_app, user_id=1, skill_name="crm-daily-coach",
            instruction="test",
            raise_on_error=True,
        )
    assert ei.value.status_code == 503
    assert "process_unavailable" in ei.value.body


@pytest.mark.asyncio
async def test_strict_raises_on_transport_exception(monkeypatch):
    """Transport-level failure (connection refused etc.) must raise too."""
    async def _fake_dispatch(*, app, envelope, bearer_key, timeout_sec):
        raise ConnectionError("ECONNREFUSED 127.0.0.1:9100")

    monkeypatch.setattr(
        "hermes_cloud.app.proxy.dispatch.dispatch_to_hermes",
        _fake_dispatch,
    )

    fake_app = SimpleNamespace(state=SimpleNamespace(
        config=SimpleNamespace(internal_key="x"),
    ))

    with pytest.raises(UtilitySkillError) as ei:
        await run_utility_skill(
            app=fake_app, user_id=1, skill_name="crm-daily-coach",
            instruction="test",
            raise_on_error=True,
        )
    assert ei.value.status_code == 502
    assert "ECONNREFUSED" in ei.value.reason


@pytest.mark.asyncio
async def test_strict_decodes_bytes_body_for_json_safe_transit(monkeypatch):
    """raw_body comes back as bytes from httpx; the cloud endpoint
    re-raises as HTTPException with the body in detail. FastAPI then
    json-serializes detail — and bytes is not JSON-serializable, so we
    must decode before storing on the exception. Without this,
    a downstream 5xx silently turns into a cloud-side 500 with a
    misleading TypeError that buries the real error."""
    async def _fake_dispatch(*, app, envelope, bearer_key, timeout_sec):
        return _DispatchResult(
            status_code=503,
            raw_body=b'{"error":{"message":"hermes transport: ","code":"process_unavailable"}}',
            reply_text="",
        )

    monkeypatch.setattr(
        "hermes_cloud.app.proxy.dispatch.dispatch_to_hermes",
        _fake_dispatch,
    )

    fake_app = SimpleNamespace(state=SimpleNamespace(
        config=SimpleNamespace(internal_key="x"),
    ))

    with pytest.raises(UtilitySkillError) as ei:
        await run_utility_skill(
            app=fake_app, user_id=1, skill_name="x",
            instruction="x", raise_on_error=True,
        )
    # Must be str, not bytes — JSON-serializable.
    assert isinstance(ei.value.body, str)
    assert "process_unavailable" in ei.value.body
    # Smoke-test JSON serialization (the actual FastAPI failure mode).
    import json
    json.dumps({"detail": {"body": ei.value.body}})  # no TypeError


@pytest.mark.asyncio
async def test_strict_returns_none_on_empty_reply(monkeypatch):
    """200 OK + empty reply is a LEGITIMATE answer ("agent decided 0
    cards"). Strict mode must NOT raise here — only on real errors."""
    async def _fake_dispatch(*, app, envelope, bearer_key, timeout_sec):
        return _DispatchResult(
            status_code=200, raw_body="{}", reply_text="",
        )

    monkeypatch.setattr(
        "hermes_cloud.app.proxy.dispatch.dispatch_to_hermes",
        _fake_dispatch,
    )

    fake_app = SimpleNamespace(state=SimpleNamespace(
        config=SimpleNamespace(internal_key="x"),
    ))

    reply = await run_utility_skill(
        app=fake_app, user_id=1, skill_name="crm-daily-coach",
        instruction="test",
        raise_on_error=True,
    )
    assert reply is None  # 0 results is fine


@pytest.mark.asyncio
async def test_lenient_default_still_returns_none_on_failure(monkeypatch):
    """Direct callers (insight scanners) rely on None-on-failure for
    their fallback paths — preserve backward compat."""
    async def _fake_dispatch(*, app, envelope, bearer_key, timeout_sec):
        return _DispatchResult(
            status_code=500, raw_body='{"err":"x"}', reply_text="",
        )

    monkeypatch.setattr(
        "hermes_cloud.app.proxy.dispatch.dispatch_to_hermes",
        _fake_dispatch,
    )

    fake_app = SimpleNamespace(state=SimpleNamespace(
        config=SimpleNamespace(internal_key="x"),
    ))

    # Default raise_on_error=False — must NOT raise.
    reply = await run_utility_skill(
        app=fake_app, user_id=1, skill_name="crm-daily-coach",
        instruction="test",
    )
    assert reply is None

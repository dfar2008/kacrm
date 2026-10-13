"""Follow-up adoptions for v2026.4.23 (A4 prompt / A5 reaction lifecycle
/ B7 PgSessionDB chat metadata writes).

Each test pins a specific contract. They run without live PG except
where noted (B7 hits the real PG since it tests SQL semantics).
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# A4 (DELETED 2026-05-05) — intake specialist was folded into the
# crm-intake markdown skill. The @mention awareness contract now lives
# in hermes_cloud/system_skills_seed/crm-intake/SKILL.md, not in a
# Python build_spec. If we want to keep this guard, write a new test
# that scans the skill markdown instead.
# ---------------------------------------------------------------------------


# NOTE: test_dispatch_gateway_approval_adds_done_or_crossmark was removed
# in the per-user-channel-bot migration (Phase 99). It introspected the
# cloud-side FeishuLongConnClient, which no longer exists — feishu
# inbound + approval reactions now run natively in each profile's
# hermes subprocess (hermes' own FeishuAdapter), not in the cloud.


# ---------------------------------------------------------------------------
# B7 — PgSessionDB.create_session writes chat metadata when present
# ---------------------------------------------------------------------------


def test_create_session_adds_no_new_required_params():
    """``create_session`` must never add a NEW *required* (no-default) param,
    so every existing upstream call site (gateway/run.py / cli.py / etc.)
    keeps working. Optional params and ``**kwargs`` ARE allowed and expected —
    ``**kwargs`` is how PgSessionDB absorbs forward-compatible upstream params
    without breaking on upgrade.

    This replaces the old ``test_create_session_signature_unchanged`` exact-
    signature pin, which wrongly went RED the moment we added ``**kwargs`` (the
    fix). Full call-compatibility with the upstream ``_SqliteSessionDB`` is now
    guarded by ``tests/hermes_cloud/test_session_db_contract.py``.
    """
    from hermes_cloud.app.agent_session_pg import PgSessionDB
    sig = inspect.signature(PgSessionDB.create_session)
    required = {
        p.name
        for p in sig.parameters.values()
        if p.name != "self"
        and p.default is inspect.Parameter.empty
        and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
    }
    assert required <= {"session_id", "source"}, (
        f"create_session grew a new REQUIRED param (breaks existing call "
        f"sites): {sorted(required)}"
    )


def test_create_session_inserts_new_metadata_columns():
    """The INSERT must include chat_name + thread_id columns so the
    additive alembic-0034 schema actually receives values when present.
    """
    from hermes_cloud.app.agent_session_pg import PgSessionDB
    src = inspect.getsource(PgSessionDB.create_session)
    # Column names appear in the INSERT.
    assert "chat_name" in src
    assert "thread_id" in src
    # Best-effort import of the gateway contextvar (cloud-only path).
    assert "gateway.session_context" in src


def test_create_session_pulls_metadata_from_session_context_when_available():
    """When ``gateway.session_context.get_session_env`` is importable
    AND the contextvar is set, create_session reads chat_name +
    thread_id from there. Patch the import to verify the path runs.
    """
    from hermes_cloud.app.agent_session_pg import PgSessionDB

    captured = []

    fake_db = MagicMock()
    fake_db.execute.side_effect = lambda sql, params: captured.append(params)
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=fake_db)
    cm.__exit__ = MagicMock(return_value=False)

    fake_get_env = MagicMock(side_effect=lambda key, default="": {
        "HERMES_SESSION_CHAT_NAME": "QA Group",
        "HERMES_SESSION_THREAD_ID": "thread-42",
    }.get(key, default))

    fake_module = MagicMock()
    fake_module.get_session_env = fake_get_env

    with patch.dict(
        "sys.modules",
        {"gateway": MagicMock(), "gateway.session_context": fake_module},
    ), patch("hermes.crm.db.db_session", return_value=cm):
        db = PgSessionDB()
        db.create_session(
            session_id="s-meta",
            source="feishu",
            user_id="42",
        )

    assert captured, "create_session did not call db.execute"
    # The last 2 params are chat_name + thread_id (per the INSERT order).
    params = captured[0]
    assert "QA Group" in params
    assert "thread-42" in params


def test_create_session_no_session_context_writes_null():
    """Outside a gateway request (no ``gateway.session_context`` import,
    or contextvar empty), the new columns must be left NULL — additive,
    backwards-compatible."""
    from hermes_cloud.app.agent_session_pg import PgSessionDB

    captured = []
    fake_db = MagicMock()
    fake_db.execute.side_effect = lambda sql, params: captured.append(params)
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=fake_db)
    cm.__exit__ = MagicMock(return_value=False)

    # Block the gateway.session_context import so the fallback fires.
    with patch.dict("sys.modules", {"gateway.session_context": None}), \
         patch("hermes.crm.db.db_session", return_value=cm):
        db = PgSessionDB()
        db.create_session(
            session_id="s-no-meta",
            source="cli",
            user_id="42",
        )

    params = captured[0]
    # Last 2 positional args are (chat_name, thread_id) — both None.
    assert params[-2] is None
    assert params[-1] is None

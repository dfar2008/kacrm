"""Regression test: copilot ``/crm/send`` must not hold a DB connection
across ``dispatch_to_hermes``.

Mirror of ``test_dashboards_chat_no_db_leak.py`` — same bug, different
route. Cloud's ``app.state.http_client`` waited for a 4+ minute hermes
response while ``Depends(get_db)`` pinned a PG connection in "idle in
transaction". After ~20 chat turns the pool was exhausted and the
whole cloud locked up.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator
from unittest.mock import MagicMock, patch


class _SessionTracker:
    def __init__(self) -> None:
        self.current = 0
        self.max_concurrent = 0
        self.open_count = 0
        self.open_during_dispatch = False

    @contextmanager
    def session(self) -> Iterator[MagicMock]:
        self.current += 1
        self.open_count += 1
        self.max_concurrent = max(self.max_concurrent, self.current)
        db = MagicMock()
        try:
            yield db
        finally:
            self.current -= 1


def test_require_user_does_not_use_depends_get_db():
    """``_require_user`` must open its own short db_session — NOT use
    ``Depends(get_db)``. FastAPI request-scoped deps stay alive until
    the response body is sent, which for ``/crm/send`` is across the
    long hermes dispatch.
    """
    import inspect
    from hermes_cloud.app.copilot.routes import _require_user

    sig = inspect.signature(_require_user)
    for name, param in sig.parameters.items():
        # No parameter may carry a ``Depends(get_db)`` default — that's
        # the exact anti-pattern this test pins down.
        default_repr = repr(param.default)
        assert "get_db" not in default_repr, (
            f"_require_user parameter {name!r} still uses Depends(get_db): "
            f"{default_repr}. Open a short db_session inside the function "
            f"body instead so the connection releases before the long "
            f"dispatch_to_hermes call."
        )


def test_copilot_send_does_not_inject_db():
    """The ``/crm/send`` route signature must not accept ``db`` via
    ``Depends(get_db)`` — same reason as above."""
    # Import the registered route directly via FastAPI router introspection.
    import inspect
    from hermes_cloud.app.copilot import routes as copilot_routes

    # Grab the inner function by source inspection — it's defined inside
    # ``register_routes`` so we walk source rather than importing.
    src = inspect.getsource(copilot_routes)
    # Find the function definition block for copilot_crm_send.
    start = src.find("async def copilot_crm_send(")
    assert start != -1, "could not find copilot_crm_send in source"
    # Signature ends at the matching ``) -> ...:`` (return annotation)
    # or plain ``):`` — we look for the first ``->`` AFTER start to
    # bound the signature reliably without false-positives from comment
    # blocks below.
    arrow = src.find("->", start)
    assert arrow != -1, "could not find return-type arrow"
    sig_only = src[start:arrow]
    assert "Depends(get_db)" not in sig_only, (
        "copilot_crm_send still has Depends(get_db) — opens long-lived "
        f"db connection across dispatch_to_hermes. Signature:\n{sig_only}"
    )


def test_copilot_send_uses_db_session_blocks():
    """The route body should open ``with db_session()`` for short bursts
    (Phase 1: user msg + module lookup; Phase 3: assistant msg) and have
    the dispatch_to_hermes call BETWEEN them, not inside either."""
    import inspect
    from hermes_cloud.app.copilot import routes as copilot_routes

    src = inspect.getsource(copilot_routes)
    start = src.find("async def copilot_crm_send(")
    # End at the next top-level async def or end-of-file
    next_def = src.find("async def ", start + 10)
    body = src[start:next_def if next_def != -1 else len(src)]

    # At least two short db_session blocks (Phase 1 + Phase 3).
    assert body.count("with db_session()") >= 2, (
        "expected >=2 ``with db_session()`` blocks (Phase 1 + Phase 3); "
        f"got {body.count('with db_session()')}"
    )

    # The dispatch must NOT be inside a db_session block. Heuristic:
    # the line "await dispatch_to_hermes" must not be indented under a
    # ``with db_session()`` that's still open at that point.
    # Simplest robust check: find the dispatch line, then count
    # un-closed ``with db_session()`` blocks before it.
    dispatch_idx = body.find("await dispatch_to_hermes(")
    assert dispatch_idx != -1, "dispatch_to_hermes call not found"
    prefix = body[:dispatch_idx]
    opens = prefix.count("with db_session()")
    # Each db_session block is followed by "# ← db released" comment OR
    # ends at dedent. Count "# ← db released" as proxy for closures
    # (we wrote them in the patch).
    closes = prefix.count("# ← db released")
    assert opens == closes, (
        f"dispatch_to_hermes called while a db_session is still open "
        f"(opens={opens}, closes={closes} in prefix). The fix must put "
        f"dispatch AFTER the Phase 1 with-block exits."
    )

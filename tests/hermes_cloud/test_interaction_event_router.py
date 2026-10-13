from __future__ import annotations

from contextlib import contextmanager


def test_should_route_kind_for_chat():
    from hermes_cloud.app.interactions.router import should_route_kind

    assert should_route_kind("chat") is True


def test_should_not_route_unknown_kind():
    from hermes_cloud.app.interactions.router import should_route_kind

    assert should_route_kind("totally_unknown_kind") is False


def test_emit_survives_router_failure(monkeypatch):
    from hermes_cloud.app.interactions import emitter
    import hermes_cloud.app.interactions.router as router

    class _FakeDb:
        def fetchone(self, sql, args):  # noqa: ARG002
            return {"id": "11111111-1111-1111-1111-111111111111"}

        def _maybe_commit(self, commit):  # noqa: ARG002
            return None

    @contextmanager
    def _fake_db_session():
        yield _FakeDb()

    def _boom(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("router exploded")

    monkeypatch.setattr(emitter, "db_session", _fake_db_session)
    monkeypatch.setattr(router, "route_new_interaction", _boom)

    interaction_id = emitter.emit(
        kind="chat",
        subjects=[{"module": "probe", "id": 1}],
        actors=[{"type": "user", "id": 1}],
        content={"text": "hello"},
        source="chat",
    )
    assert interaction_id == "11111111-1111-1111-1111-111111111111"

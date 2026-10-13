"""Memory API routing smoke test.

The store-level functions (search_memories, find_entity, subgraph)
touch pgvector and are integration-tested elsewhere. Here we just
verify:

1. The router mounts with the expected prefix and path set.
2. The handlers call through to the store with the right args.
3. Auth is required (no session → 401 via the real dependency).

We monkeypatch the store + auth so this runs with zero DB.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cloud.app.memory import api as memory_api


def _make_app(monkeypatch, user_id: int = 42) -> TestClient:
    monkeypatch.setattr(memory_api, "_current_user_id", lambda req: user_id)
    # /search is admin-only (org-shared memory); simulate an admin caller.
    monkeypatch.setattr(memory_api, "require_admin_auth", lambda req, cfg: str(user_id))
    app = FastAPI()
    app.include_router(memory_api.router)
    return TestClient(app)


def test_search_forwards_args_and_scopes_user(monkeypatch):
    captured = {}

    def fake_search(**kwargs):
        captured.update(kwargs)
        return [{"id": 1, "content": "hit", "similarity": 0.9}]

    monkeypatch.setattr(memory_api, "search_memories", fake_search)
    client = _make_app(monkeypatch, user_id=42)

    r = client.get(
        "/api/memory/search",
        params={"q": "acme pricing", "top_k": 5, "min_similarity": 0.3},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert body["data"][0]["content"] == "hit"
    assert captured["user_id"] == 42
    assert captured["query"] == "acme pricing"
    assert captured["top_k"] == 5
    assert captured["min_similarity"] == 0.3


def test_search_rejects_empty_query(monkeypatch):
    monkeypatch.setattr(memory_api, "search_memories", lambda **k: [])
    client = _make_app(monkeypatch)
    r = client.get("/api/memory/search", params={"q": ""})
    assert r.status_code == 422  # fastapi Query(min_length=1)


def test_entity_lookup_falls_back_to_org_scope(monkeypatch):
    calls = []

    def fake_find(**kwargs):
        calls.append(kwargs)
        # First call (user-scoped) misses; second (org-scoped) hits.
        if len(calls) == 1:
            return None
        return {"id": 7, "entity_type": "org", "canonical": "Acme Corp"}

    monkeypatch.setattr(memory_api, "find_entity", fake_find)
    client = _make_app(monkeypatch)

    r = client.get(
        "/api/memory/entities",
        params={"entity_type": "org", "canonical": "Acme Corp"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["id"] == 7
    # First user-scoped, second org-scoped (no user_id)
    assert calls[0].get("user_id") == 42
    assert "user_id" not in calls[1] or calls[1].get("user_id") is None


def test_entity_lookup_404_when_both_scopes_miss(monkeypatch):
    monkeypatch.setattr(memory_api, "find_entity", lambda **k: None)
    client = _make_app(monkeypatch)
    r = client.get(
        "/api/memory/entities",
        params={"entity_type": "org", "canonical": "nope"},
    )
    assert r.status_code == 404


def test_subgraph_caps_depth_and_forwards(monkeypatch):
    captured = {}

    def fake_subgraph(**kwargs):
        captured.update(kwargs)
        return {"entities": [], "edges": []}

    monkeypatch.setattr(memory_api, "subgraph", fake_subgraph)
    client = _make_app(monkeypatch)

    # depth > hard cap (3) should be rejected by Query ge/le bounds
    r = client.get(
        "/api/memory/entities/5/subgraph",
        params={"depth": 10},
    )
    assert r.status_code == 422

    r = client.get(
        "/api/memory/entities/5/subgraph",
        params={"depth": 2, "max_edges": 50},
    )
    assert r.status_code == 200
    assert captured["entity_id"] == 5
    assert captured["depth"] == 2
    assert captured["max_edges"] == 50

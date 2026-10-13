"""Memory entity merge — dedup tool logic.

The actual SQL needs Postgres, but the public contract (aliases +
attributes merge, confidence max, self-merge rejection, API shape) is
testable with a thin fake.
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cloud.app.memory import api as memory_api
from hermes.memory import entities as entities_mod


class FakeDb:
    """In-memory double good enough to trace the merge path."""

    def __init__(self, entities, sources=None, edges=None):
        self.entities = {int(e["id"]): dict(e) for e in entities}
        self.sources = list(sources or [])
        self.edges = list(edges or [])
        self.calls = []

    def fetchone(self, sql, args):
        self.calls.append(("fetchone", sql, args))
        if "FROM memory_entities WHERE id" in sql:
            eid = int(args[0])
            return self.entities.get(eid)
        return None

    def fetchall(self, sql, args):
        self.calls.append(("fetchall", sql, args))
        return []

    def execute(self, sql, args):
        self.calls.append(("execute", sql, args))
        s = " ".join(sql.split())
        if s.startswith("UPDATE memory_entities SET aliases"):
            aliases_json, attrs_json, conf, eid = args
            e = self.entities[int(eid)]
            e["aliases"] = json.loads(aliases_json)
            e["attributes"] = json.loads(attrs_json)
            e["confidence"] = conf
        elif s.startswith("DELETE FROM memory_entities WHERE id"):
            self.entities.pop(int(args[0]), None)


def test_merge_entities_unions_aliases_and_shallow_merges_attrs():
    src = {
        "id": 1, "entity_type": "org",
        "canonical": "ACME Corp.",
        "aliases": ["acme"],
        "attributes": {"industry": "manufacturing", "only_in_src": True},
        "confidence": 0.7,
    }
    dst = {
        "id": 2, "entity_type": "org",
        "canonical": "Acme Corp",
        "aliases": ["acme corp"],
        "attributes": {"industry": "manufacturing-hw", "website": "acme.com"},
        "confidence": 0.9,
    }
    db = FakeDb(entities=[src, dst])
    result = entities_mod.merge_entities(src_id=1, dst_id=2, db=db)

    # Src row is gone; dst row updated.
    assert 1 not in db.entities
    assert result["id"] == 2
    # Union + src canonical added; dst's canonical is NOT in aliases.
    assert set(result["aliases"]) == {"acme", "acme corp", "ACME Corp."}
    # dst wins on conflicting keys.
    assert result["attributes"]["industry"] == "manufacturing-hw"
    assert result["attributes"]["only_in_src"] is True
    assert result["attributes"]["website"] == "acme.com"
    # Max confidence.
    assert result["confidence"] == 0.9


def test_merge_into_self_raises():
    db = FakeDb(entities=[{"id": 1, "canonical": "x"}])
    with pytest.raises(ValueError):
        entities_mod.merge_entities(src_id=1, dst_id=1, db=db)


def test_merge_missing_entity_raises():
    db = FakeDb(entities=[{"id": 1, "canonical": "x", "aliases": [], "attributes": {}, "confidence": 1.0}])
    with pytest.raises(ValueError):
        entities_mod.merge_entities(src_id=1, dst_id=99, db=db)


# ---- API surface ---------------------------------------------------------

def test_merge_endpoint_requires_src_id(monkeypatch):
    import hermes_cloud.app.auth as auth_mod
    monkeypatch.setattr(auth_mod, "require_admin_auth", lambda req, cfg: 1)
    monkeypatch.setattr(memory_api, "require_admin_auth", lambda req, cfg: 1)
    app = FastAPI()
    app.include_router(memory_api.router)
    client = TestClient(app)

    r = client.post("/api/memory/entities/2/merge", json={})
    assert r.status_code == 400
    assert "src_id" in r.json()["detail"]


def test_merge_endpoint_calls_store(monkeypatch):
    monkeypatch.setattr(memory_api, "require_admin_auth", lambda req, cfg: 1)
    captured = {}

    def fake_merge(*, src_id, dst_id):
        captured["src"] = src_id
        captured["dst"] = dst_id
        return {"id": dst_id, "canonical": "Acme Corp", "aliases": []}

    monkeypatch.setattr(memory_api, "merge_entities", fake_merge)
    app = FastAPI()
    app.include_router(memory_api.router)
    client = TestClient(app)

    r = client.post("/api/memory/entities/2/merge", json={"src_id": 7})
    assert r.status_code == 200, r.text
    assert captured == {"src": 7, "dst": 2}
    assert r.json()["data"]["id"] == 2

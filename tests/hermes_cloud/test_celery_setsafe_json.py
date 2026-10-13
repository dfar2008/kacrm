"""Regression: Celery's JSON task serializer must tolerate ``set`` values.

Root cause of the silent ``after_commit_callback_failed
err=EncodeError(TypeError('Object of type set is not JSON serializable'))``:
a CRM record write registers ``db.after_commit(...)`` callbacks that call
``run_workflow_task.apply_async(...)`` / ``ai_fill_task.apply_async(...)``.
Celery (``task_serializer="json"``) serializes the task args through
kombu; a stray ``set`` (e.g. a ``changed_fields`` diff) made kombu raise
``EncodeError``, which ``DbWrapper._drain_after_commit`` swallows — so the
workflow / AI-fill dispatch (and any approval notification it would emit)
vanished without a trace.

The fix hardens the serialization *boundary* once: importing the Celery
app registers a set-safe JSON serializer so no task arg can ever break a
dispatch this way again. These tests pin that behavior and guard the
existing kombu typed-encoding (datetime / uuid) we must not regress.
"""
from __future__ import annotations

import datetime as _dt
import uuid as _uuid

# Importing the app triggers the set-safe serializer registration as a
# module import side effect (producers import the app to enqueue tasks).
import hermes_cloud.app.celery_app  # noqa: F401

from kombu.serialization import dumps, loads


def _roundtrip(obj):
    content_type, content_encoding, data = dumps(obj, "json")
    return loads(data, content_type, content_encoding)


def test_kombu_json_serializer_handles_set():
    """The exact production payload shape must serialize without raising."""
    out = _roundtrip({"changed_fields": {"industry", "name"}})
    # set -> deterministically sorted list (order-stable for run_key reuse).
    assert out == {"changed_fields": ["industry", "name"]}


def test_kombu_json_serializer_handles_frozenset_and_nested():
    out = _roundtrip({"a": frozenset({"z", "a"}), "b": [{"c": {"q"}}]})
    assert out == {"a": ["a", "z"], "b": [{"c": ["q"]}]}


def test_kombu_json_serializer_handles_empty_set():
    assert _roundtrip({"cf": set()}) == {"cf": []}


def test_kombu_json_serializer_preserves_typed_encoding():
    """Subclassing kombu's JSONEncoder must keep datetime / uuid fidelity —
    kombu round-trips these via typed markers; we must not have broken it."""
    when = _dt.datetime(2026, 6, 25, 12, 0, tzinfo=_dt.timezone.utc)
    uid = _uuid.uuid4()
    out = _roundtrip({"when": when, "uid": uid})
    assert out["when"] == when
    assert out["uid"] == uid


def test_emitter_to_jsonb_handles_set():
    """Defense in depth: the interactions JSONB encoder must coerce a stray
    set rather than abort the business write."""
    import json
    from hermes_cloud.app.interactions.emitter import _to_jsonb

    encoded = _to_jsonb({"fields": {"name", "industry"}})
    assert json.loads(encoded) == {"fields": ["industry", "name"]}


def test_action_gateway_json_default_handles_set():
    """Defense in depth: the action_gateway dispatcher serializes
    ``params``/``origin`` at write time — a stray set there must coerce to a
    list rather than roll back the whole gateway write with a TypeError."""
    import json
    from hermes.action_gateway.dispatcher import _json_or_null

    encoded = _json_or_null({"tags": {"vip", "lead"}})
    assert json.loads(encoded) == {"tags": ["lead", "vip"]}

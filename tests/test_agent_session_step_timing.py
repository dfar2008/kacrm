import os, json, time, pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("HERMES_TESTS_REAL_PG") != "1",
    reason="needs real PG",
)


def _store():
    from hermes_cloud.app.agent_session_pg import PgSessionDB
    return PgSessionDB()


def test_append_message_persists_duration_and_turn_meta():
    db = _store()
    sid = f"test-timing-{int(time.time()*1000)}"
    db.create_session(sid, source="test:timing", user_id="1", model="big-pickle")
    mid = db.append_message(
        sid, "assistant", content="done",
        duration_ms=5700,
        turn_meta={"total_ms": 282600, "queued_ms": 40000, "api_calls": 2, "tool_count": 1},
    )
    rows = db.get_messages(sid)
    last = [r for r in rows if r["id"] == mid][0]
    assert last["duration_ms"] == 5700
    tm = last["turn_meta"] if isinstance(last["turn_meta"], dict) else json.loads(last["turn_meta"])
    assert tm["total_ms"] == 282600 and tm["api_calls"] == 2


def test_set_turn_meta_stamps_last_assistant():
    db = _store()
    sid = f"test-tm-{int(time.time()*1000)}"
    db.create_session(sid, source="test:timing", user_id="1", model="big-pickle")
    db.append_message(sid, "user", content="hi")
    a1 = db.append_message(sid, "assistant", content="first", duration_ms=100)
    a2 = db.append_message(sid, "assistant", content="final reply", duration_ms=5700)
    ok = db.set_turn_meta(sid, {"total_ms": 282600, "queued_ms": 40000, "api_calls": 2, "tool_count": 1})
    assert ok is True
    rows = {r["id"]: r for r in db.get_messages(sid)}
    tm = rows[a2]["turn_meta"]
    tm = tm if isinstance(tm, dict) else json.loads(tm)
    assert tm["total_ms"] == 282600 and tm["tool_count"] == 1
    # earlier assistant must NOT be stamped
    assert rows[a1]["turn_meta"] in (None, "null")

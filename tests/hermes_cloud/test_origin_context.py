"""``bridge.origin_context`` — per-invocation channel binding.

Covers Charter §1 渠道闭环 plumbing:

* ``current()`` defaults to ``{"channel": "webui"}`` so callers that
  forget to bind() still get a safe origin.
* ``scope()`` binds for the duration of a ``with`` block and reverts
  cleanly even on exception.
* Channel name normalization handles the source aliases the touchpoint
  adapters historically emit (``feishu``/``lark``, ``wecom``/``wechat``,
  ``chat``/``manual``/``note`` → ``webui``).
* ``from_interaction_actors`` walks the actors list, prefers a user
  actor over any other, and folds in ``external_*`` IDs the channel
  adapter stored alongside ``source``.
* Concurrent contexts (asyncio / threads) cannot leak origins between
  each other — that's what contextvars are for; we sanity-check via
  threading and asyncio that bindings don't bleed.
"""
from __future__ import annotations

import asyncio
import threading


def test_current_defaults_to_webui_when_unbound():
    from hermes_cloud.app.bridge import origin_context
    out = origin_context.current()
    assert out == {"channel": "webui"}
    # Returns a fresh dict — callers can't mutate the binding.
    out["channel"] = "MUTATED"
    assert origin_context.current() == {"channel": "webui"}


def test_scope_binds_and_reverts():
    from hermes_cloud.app.bridge import origin_context

    with origin_context.scope({"channel": "feishu", "external_user_id": "ou_x"}):
        assert origin_context.current()["channel"] == "feishu"
        assert origin_context.current()["external_user_id"] == "ou_x"
    assert origin_context.current() == {"channel": "webui"}


def test_scope_reverts_on_exception():
    from hermes_cloud.app.bridge import origin_context

    try:
        with origin_context.scope({"channel": "discord"}):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert origin_context.current() == {"channel": "webui"}


def test_invalid_origin_falls_back_to_default():
    from hermes_cloud.app.bridge import origin_context

    # Missing channel → treated as None.
    with origin_context.scope({"external_user_id": "ou_x"}):
        assert origin_context.current() == {"channel": "webui"}

    # Empty dict → default.
    with origin_context.scope({}):
        assert origin_context.current() == {"channel": "webui"}


def test_from_interaction_actors_picks_user_with_source():
    from hermes_cloud.app.bridge import origin_context

    actors = [
        {"type": "agent", "name": "intake"},
        {
            "type": "user",
            "id": 7,
            "source": "feishu",
            "external_user_id": "ou_abc",
            "external_message_id": "om_xyz",
        },
    ]
    out = origin_context.from_interaction_actors(
        actors=actors, interaction_id="iid-1",
    )
    assert out["channel"] == "feishu"
    assert out["external_user_id"] == "ou_abc"
    assert out["external_message_id"] == "om_xyz"
    assert out["interaction_id"] == "iid-1"


def test_from_interaction_actors_normalizes_aliases():
    from hermes_cloud.app.bridge import origin_context

    cases = {
        "lark": "feishu",
        "wecom": "wecom_ilink",
        "wechat": "wecom_ilink",
        "chat": "webui",
        "manual": "webui",
        "note": "webui",
        "reclassify": "webui",
        "discord": "discord",
        "feishu_card": "feishu",  # heuristic prefix
    }
    for source, expected in cases.items():
        out = origin_context.from_interaction_actors(
            actors=[{"type": "user", "id": 1, "source": source}],
        )
        assert out["channel"] == expected, f"{source} → {expected}"


def test_from_interaction_actors_no_actors_falls_back():
    from hermes_cloud.app.bridge import origin_context

    out = origin_context.from_interaction_actors(actors=None)
    assert out == {"channel": "webui"}

    out = origin_context.from_interaction_actors(actors=[])
    assert out == {"channel": "webui"}

    # Actors present but no source — still webui.
    out = origin_context.from_interaction_actors(
        actors=[{"type": "user", "id": 1}],
    )
    assert out["channel"] == "webui"


def test_contextvar_isolates_threads():
    """Two threads each bind their own origin; neither sees the other's."""
    from hermes_cloud.app.bridge import origin_context

    seen: dict = {}
    barrier = threading.Barrier(2)

    def worker(name: str, channel: str):
        with origin_context.scope({"channel": channel}):
            barrier.wait()
            # Both threads now hold their own bindings simultaneously.
            seen[name] = origin_context.current()["channel"]

    t1 = threading.Thread(target=worker, args=("a", "feishu"))
    t2 = threading.Thread(target=worker, args=("b", "discord"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    # Each thread saw its own binding; no cross-contamination.
    assert seen == {"a": "feishu", "b": "discord"}


def test_contextvar_isolates_asyncio_tasks():
    """Concurrent asyncio tasks each get their own origin."""
    from hermes_cloud.app.bridge import origin_context

    seen: dict = {}

    async def task(name: str, channel: str):
        with origin_context.scope({"channel": channel}):
            await asyncio.sleep(0)  # yield so both tasks interleave
            seen[name] = origin_context.current()["channel"]

    async def main():
        await asyncio.gather(task("a", "wecom_ilink"), task("b", "telegram"))

    asyncio.run(main())
    assert seen == {"a": "wecom_ilink", "b": "telegram"}

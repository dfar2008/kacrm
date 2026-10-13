"""Upgrade-acceptance — clarify choice normalization (module D).

Guards the class of bug where a model emits choices in a non-flat shape and
hermes drops them silently, turning a multiple-choice clarify into a broken
open-ended one (the IM user sees the question but no options, while the SPA
inbox still shows the raw stored tool args). Runnable against ANY hermes
version to catch upgrade regressions in ``_flatten_choice`` / clarify.

Real-world trigger (2026-07-03, wangz feishu): the model emitted
``[{"type": {"value": "短篇精品…"}}]`` and every option vanished.
"""

from __future__ import annotations

import json

import pytest

from tools.clarify_tool import _flatten_choice, clarify_tool


class TestFlattenChoiceShapes:
    """``_flatten_choice`` must recover the human label from every shape a
    model realistically emits, and only drop genuinely label-less dicts."""

    def test_bare_string(self):
        assert _flatten_choice("短篇精品") == "短篇精品"

    def test_strips_whitespace(self):
        assert _flatten_choice("  hi  ") == "hi"

    @pytest.mark.parametrize("key", ["label", "description", "text", "title"])
    def test_canonical_keys(self, key):
        assert _flatten_choice({key: "长篇连载"}) == "长篇连载"

    def test_value_key_recovered(self):
        # Previously dropped ("value" excluded). A visible choice beats a
        # silently-vanished one.
        assert _flatten_choice({"value": "你来定题材"}) == "你来定题材"

    def test_nested_type_value_recovered(self):
        # The exact real-world failure shape.
        assert (
            _flatten_choice({"type": {"value": "短篇精品(3000-5000字一章写完)"}})
            == "短篇精品(3000-5000字一章写完)"
        )

    def test_canonical_wins_over_value(self):
        # A real label must still take priority over a component-shaped value.
        assert _flatten_choice({"label": "好", "value": "OPT_A"}) == "好"

    def test_nested_list_joined(self):
        assert _flatten_choice(["a", "b"]) == "a b"

    def test_labelless_dict_dropped(self):
        assert _flatten_choice({"foo": 1, "bar": 2}) == ""

    def test_none_dropped(self):
        assert _flatten_choice(None) == ""


class TestClarifyToolEndToEnd:
    """The whole tool: malformed choices must survive to ``choices_offered``
    (what the callback / send_clarify receives), not collapse to open-ended."""

    def test_nested_choices_reach_callback(self):
        seen = {}

        def _cb(questions):
            seen["q"] = questions[0]["question"]
            seen["choices"] = questions[0]["choices"]
            return {"answers": {"q0": "短篇精品(3000-5000字一章写完)"}, "outcome": "submitted"}

        # With a callback, clarify_tool returns the user's answer string; the
        # contract we guard is what the callback (→ send_clarify) RECEIVED.
        clarify_tool(
            questions=[{"question": "万万字故事,咱们怎么搞?",
            "choices": [
                {"type": {"value": "短篇精品(3000-5000字一章写完)"}},
                {"type": {"value": "长篇连载(每章2000-3000字,分多次发)"}},
                {"type": {"value": "你来定题材+开篇我来写"}},
            ]}],
            callback=_cb,
        )
        # The three options must be recovered as flat strings, not dropped.
        # v0.20: upstream decorates the FIRST choice with "(Recommended)"
        # (tools/clarify_tool.RECOMMENDED_LABEL); strip_recommended() removes
        # it from the resolved answer, so the decoration is presentation-only.
        assert seen["choices"] == [
            "短篇精品(3000-5000字一章写完) (Recommended)",
            "长篇连载(每章2000-3000字,分多次发)",
            "你来定题材+开篇我来写",
        ]

    def test_labelless_choices_fail_without_stranding_a_card(self):
        called = []
        result = json.loads(clarify_tool(
            [{"question": "pick", "choices": [{"foo": 1}, {"bar": 2}]}],
            callback=lambda questions: called.append(questions),
        ))
        assert "all are blank" in result["error"]
        assert not called

    def test_omitted_choices_remain_open_ended(self):
        seen = []
        def callback(questions):
            seen.extend(questions)
            return {"answers": {"q0": "whatever"}, "outcome": "submitted"}
        result = json.loads(clarify_tool([{"question": "pick"}], callback=callback))
        assert seen[0]["choices"] is None
        assert result["responses"][0]["user_response"] == "whatever"


class TestSchemaTagStripping:
    """Some models leak JSON-schema / XML tags into the emitted strings — e.g. a
    choice value ``短篇精品（…）</value>`` or a question with a trailing tag. These
    must be scrubbed so a stray ``</value>`` never renders to the user (the SPA
    showed one after the clarify question, wangz feishu, 2026-07)."""

    def test_flatten_strips_trailing_value_tag(self):
        assert _flatten_choice("短篇精品（3000-5000字）</value>") == "短篇精品（3000-5000字）"

    def test_flatten_strips_nested_value_with_tag(self):
        assert _flatten_choice({"type": {"value": "长篇连载</value>"}}) == "长篇连载"

    def test_flatten_strips_wrapping_value_tags(self):
        assert _flatten_choice("<value>你来定题材</value>") == "你来定题材"

    def test_question_stripped_of_schema_tags(self):
        seen = {}

        def cb(questions):
            seen["q"] = questions[0]["question"]
            return {"answers": {"q0": "x"}, "outcome": "submitted"}

        clarify_tool([{"question": "万万字故事,咱们怎么搞?</value>"}], callback=cb)
        assert "</value>" not in seen["q"] and "<value>" not in seen["q"]
        assert "搞?" in seen["q"]


class TestLabelAsKeyShape:
    """MiniMax-M3.0 also emits the label AS the dict key with an empty value —
    ``{"科幻/未来": ""}`` (wangz feishu, 2026-07). The robust normalizer pulls
    the label-like string from ANYWHERE in the dict (values, nested, keys), so
    whatever shape the model invents, a real option is recovered instead of
    silently dropped."""

    def test_label_as_dict_key_recovered(self):
        assert _flatten_choice({"科幻/未来": ""}) == "科幻/未来"
        assert _flatten_choice({"都市/职场": ""}) == "都市/职场"

    def test_label_as_key_still_drops_bare_id(self):
        # key not label-like (short ASCII id) → dropped (upstream leak guard)
        assert _flatten_choice({"x": ""}) == ""

    def test_key_shaped_choices_reach_callback(self):
        seen = {}

        def cb(questions):
            seen["c"] = questions[0]["choices"]
            return {"answers": {"q0": "科幻/未来"}, "outcome": "submitted"}

        clarify_tool(
            [{"question": "想听哪种?",
              "choices": [{"科幻/未来": ""}, {"悬疑/推理": ""}, {"你来定方向": ""}]}],
            callback=cb,
        )
        # v0.20: first choice carries the upstream "(Recommended)" decoration.
        assert seen["c"] == ["科幻/未来 (Recommended)", "悬疑/推理", "你来定方向"]

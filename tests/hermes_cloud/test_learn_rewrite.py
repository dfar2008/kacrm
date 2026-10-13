"""③b /learn —— chat-completions 请求体改写的单元测试。

只覆盖纯函数 ``maybe_rewrite_learn_body`` / ``_extract_learn_request`` /
``build_learn_prompt``,不触碰 hermes / DB,可在隔离环境直接跑。
"""
import json

import pytest

from hermes_cloud.app.learn import (
    _extract_learn_request,
    build_learn_prompt,
    maybe_rewrite_learn_body,
)


def _body(messages):
    return json.dumps({"messages": messages, "stream": True}).encode("utf-8")


# ── _extract_learn_request ────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("/learn", ""),
    ("/learn ", ""),
    ("/learn 聚焦报价审批流程", "聚焦报价审批流程"),
    ("  /learn  多余空白  ", "多余空白"),
    ("/LEARN 大小写不敏感", "大小写不敏感"),
    ("/learn\n换行分隔的聚焦", "换行分隔的聚焦"),
])
def test_extract_learn_request_hits(text, expected):
    assert _extract_learn_request(text) == expected


@pytest.mark.parametrize("text", [
    "",
    "帮我建个客户",
    "请解释一下 /learn 是什么",   # 正文里提到但非以之开头 → 不触发
    "learn 无斜杠",
])
def test_extract_learn_request_misses(text):
    assert _extract_learn_request(text) is None


# ── build_learn_prompt ────────────────────────────────────────────────────
def test_build_learn_prompt_nonempty_and_mentions_skill_manage():
    p = build_learn_prompt("聚焦报价审批")
    assert isinstance(p, str) and len(p) > 100
    assert "skill_manage" in p
    assert "聚焦报价审批" in p


def test_build_learn_prompt_empty_uses_this_conversation_default():
    p = build_learn_prompt("")
    assert isinstance(p, str) and len(p) > 100
    # 空请求 → 默认提炼「这个对话」(hermes 版本英文,fallback 中文,两者都提到会话)
    assert ("conversation" in p) or ("对话" in p)


# ── maybe_rewrite_learn_body ──────────────────────────────────────────────
def test_rewrite_string_content_learn_turn():
    raw = _body([
        {"role": "user", "content": "建个客户字节跳动"},
        {"role": "assistant", "content": "已创建。"},
        {"role": "user", "content": "/learn 把这套录单流程存下来"},
    ])
    out = maybe_rewrite_learn_body(raw)
    assert out is not None
    body = json.loads(out)
    # 只改最后一条 user;前两条原样。
    assert body["messages"][0]["content"] == "建个客户字节跳动"
    assert body["messages"][1]["content"] == "已创建。"
    last = body["messages"][2]["content"]
    assert "skill_manage" in last
    assert "把这套录单流程存下来" in last
    # 其余字段不动。
    assert body["stream"] is True


def test_rewrite_bare_learn():
    raw = _body([
        {"role": "user", "content": "走一套流程"},
        {"role": "assistant", "content": "好。"},
        {"role": "user", "content": "/learn"},
    ])
    out = maybe_rewrite_learn_body(raw)
    assert out is not None
    body = json.loads(out)
    assert "skill_manage" in body["messages"][2]["content"]


def test_non_learn_returns_none():
    raw = _body([
        {"role": "user", "content": "帮我查一下本月新签"},
    ])
    assert maybe_rewrite_learn_body(raw) is None


def test_rewrite_multimodal_content_keeps_non_text_blocks():
    raw = _body([
        {"role": "user", "content": [
            {"type": "text", "text": "/learn 聚焦发票录单"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
        ]},
    ])
    out = maybe_rewrite_learn_body(raw)
    assert out is not None
    blocks = json.loads(out)["messages"][0]["content"]
    assert isinstance(blocks, list)
    text_blocks = [b for b in blocks if b.get("type") == "text"]
    img_blocks = [b for b in blocks if b.get("type") == "image_url"]
    assert len(text_blocks) == 1
    assert "skill_manage" in text_blocks[0]["text"]
    assert "聚焦发票录单" in text_blocks[0]["text"]
    # 图片块原样保留
    assert len(img_blocks) == 1
    assert img_blocks[0]["image_url"]["url"].startswith("data:image/png")


def test_malformed_body_returns_none():
    assert maybe_rewrite_learn_body(b"") is None
    assert maybe_rewrite_learn_body(b"not json") is None
    assert maybe_rewrite_learn_body(json.dumps({"messages": []}).encode()) is None
    assert maybe_rewrite_learn_body(json.dumps({"messages": [
        {"role": "assistant", "content": "无 user turn"},
    ]}).encode()) is None


def test_only_latest_user_turn_is_examined():
    # 早先的 user turn 是 /learn,但最新 user turn 不是 → 不改写。
    raw = _body([
        {"role": "user", "content": "/learn 旧的"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "继续正常聊天"},
    ])
    assert maybe_rewrite_learn_body(raw) is None

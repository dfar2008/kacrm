"""Tests for profile_model_client.chat_stream — OpenAI SSE parsing."""
from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from hermes_cloud.app.llm.profile_model_client import (
    ProfileModelConfig,
    chat_stream,
)


def _cfg() -> ProfileModelConfig:
    return ProfileModelConfig(
        model="big-pickle", base_url="https://opencode.ai/zen/v1",
        api_key="sk-test", source="profile",
    )


def _sse_lines(chunks):
    """Build SSE-formatted lines from delta-content strings (+ [DONE])."""
    out = []
    for c in chunks:
        body = {"choices": [{"delta": {"content": c}}]}
        out.append(f"data: {json.dumps(body)}")
    out.append("data: [DONE]")
    return out


class _FakeResp:
    def __init__(self, lines, status=200):
        self._lines = lines
        self.status_code = status

    def iter_lines(self):
        for ln in self._lines:
            yield ln

    def read(self):
        return b""


class _FakeClient:
    def __init__(self, lines, status=200):
        self._lines = lines
        self._status = status

    def __enter__(self): return self
    def __exit__(self, *a): return False

    @contextmanager
    def stream(self, method, url, **kw):
        yield _FakeResp(self._lines, self._status)


def test_chat_stream_yields_text_chunks(monkeypatch):
    fake_client = _FakeClient(_sse_lines(["hello", " ", "world"]))
    monkeypatch.setattr(
        "hermes_cloud.app.llm.profile_model_client.httpx.Client",
        lambda **kw: fake_client,
    )
    out = list(chat_stream(cfg=_cfg(), messages=[{"role": "user", "content": "x"}]))
    assert "".join(out) == "hello world"


def test_chat_stream_handles_done_terminator(monkeypatch):
    # [DONE] should stop iteration cleanly
    fake_client = _FakeClient(["data: [DONE]"])
    monkeypatch.setattr(
        "hermes_cloud.app.llm.profile_model_client.httpx.Client",
        lambda **kw: fake_client,
    )
    out = list(chat_stream(cfg=_cfg(), messages=[{"role": "user", "content": "x"}]))
    assert out == []


def test_chat_stream_skips_blank_and_non_data_lines(monkeypatch):
    lines = ["", "ping: alive", "data: {bad json"] + _sse_lines(["abc"])
    fake_client = _FakeClient(lines)
    monkeypatch.setattr(
        "hermes_cloud.app.llm.profile_model_client.httpx.Client",
        lambda **kw: fake_client,
    )
    out = list(chat_stream(cfg=_cfg(), messages=[]))
    assert "".join(out) == "abc"


def test_chat_stream_4xx_emits_nothing(monkeypatch):
    fake_client = _FakeClient(_sse_lines(["x"]), status=403)
    monkeypatch.setattr(
        "hermes_cloud.app.llm.profile_model_client.httpx.Client",
        lambda **kw: fake_client,
    )
    out = list(chat_stream(cfg=_cfg(), messages=[]))
    assert out == []


def test_chat_stream_handles_list_content(monkeypatch):
    body = {"choices": [{"delta": {"content": [
        {"type": "text", "text": "alpha"},
        {"type": "text", "text": "beta"},
    ]}}]}
    fake_client = _FakeClient([f"data: {json.dumps(body)}", "data: [DONE]"])
    monkeypatch.setattr(
        "hermes_cloud.app.llm.profile_model_client.httpx.Client",
        lambda **kw: fake_client,
    )
    out = list(chat_stream(cfg=_cfg(), messages=[]))
    assert out == ["alpha", "beta"]

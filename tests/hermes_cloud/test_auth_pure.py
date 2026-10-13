"""Pure-function tests for :mod:`hermes_cloud.app.auth`.

Covers ``_sign``, ``_make_session_token``, and ``_verify_session_token`` —
the HMAC-based session cookie primitives. These are pure Python with no DB
or network access.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from unittest.mock import patch

import pytest

from hermes_cloud.app.auth import (
    _make_session_token,
    _sign,
    _verify_session_token,
)

_SECRET = "test-secret-key"


# ---------------------------------------------------------------------------
# _sign
# ---------------------------------------------------------------------------


def test_sign_returns_hex_string():
    result = _sign(_SECRET, "payload")
    assert isinstance(result, str)
    # SHA-256 hex digest is always 64 characters
    assert len(result) == 64


def test_sign_deterministic():
    assert _sign(_SECRET, "abc") == _sign(_SECRET, "abc")


def test_sign_different_secret_different_digest():
    assert _sign("secret-a", "payload") != _sign("secret-b", "payload")


def test_sign_different_payload_different_digest():
    assert _sign(_SECRET, "payload-a") != _sign(_SECRET, "payload-b")


def test_sign_matches_hmac_sha256():
    expected = hmac.new(
        _SECRET.encode(), "hello".encode(), hashlib.sha256
    ).hexdigest()
    assert _sign(_SECRET, "hello") == expected


# ---------------------------------------------------------------------------
# _make_session_token
# ---------------------------------------------------------------------------


def test_make_session_token_four_parts():
    tok = _make_session_token(_SECRET, "42", "apikey")
    assert len(tok.split(".")) == 4


def test_make_session_token_user_id_in_position():
    tok = _make_session_token(_SECRET, "99", "apikey")
    parts = tok.split(".")
    assert parts[1] == "99"


def test_make_session_token_timestamp_present():
    tok = _make_session_token(_SECRET, "1", "apikey")
    parts = tok.split(".")
    ts = int(parts[2])
    assert abs(ts - int(time.time())) < 5


def test_make_session_token_different_each_call():
    tok1 = _make_session_token(_SECRET, "1", "k")
    tok2 = _make_session_token(_SECRET, "1", "k")
    # Different uuid → different tokens
    assert tok1 != tok2


# ---------------------------------------------------------------------------
# _verify_session_token
# ---------------------------------------------------------------------------


def test_verify_valid_token_returns_user_id():
    tok = _make_session_token(_SECRET, "42", "apikey")
    result = _verify_session_token(_SECRET, tok)
    assert result is not None
    assert result[0] == "42"


def test_verify_returns_token_as_second_element():
    tok = _make_session_token(_SECRET, "7", "k")
    result = _verify_session_token(_SECRET, tok)
    assert result[1] == tok


def test_verify_wrong_secret_returns_none():
    tok = _make_session_token(_SECRET, "42", "apikey")
    assert _verify_session_token("wrong-secret", tok) is None


def test_verify_tampered_sig_returns_none():
    tok = _make_session_token(_SECRET, "42", "apikey")
    parts = tok.split(".")
    parts[3] = "0" * 64  # replace sig with zeros
    assert _verify_session_token(_SECRET, ".".join(parts)) is None


def test_verify_tampered_user_id_returns_none():
    tok = _make_session_token(_SECRET, "42", "apikey")
    parts = tok.split(".")
    parts[1] = "99"  # change user_id without updating sig
    assert _verify_session_token(_SECRET, ".".join(parts)) is None


def test_verify_malformed_token_too_few_parts():
    assert _verify_session_token(_SECRET, "only.three.parts") is None


def test_verify_malformed_token_too_many_parts():
    assert _verify_session_token(_SECRET, "a.b.c.d.e") is None


def test_verify_expired_token_returns_none():
    # Construct a token with timestamp far in the past (epoch + 1)
    sid = "deadbeefdeadbeef"
    user_id = "5"
    ts = "1"  # 1970-01-01 — definitely expired
    body = f"{sid}.{user_id}.{ts}"
    sig = _sign(_SECRET, body)
    tok = f"{body}.{sig}"
    assert _verify_session_token(_SECRET, tok) is None


def test_verify_empty_string_returns_none():
    assert _verify_session_token(_SECRET, "") is None


def test_verify_non_string_garbage_returns_none():
    # non-str but split-able won't crash
    assert _verify_session_token(_SECRET, "x.y.z.w") is None

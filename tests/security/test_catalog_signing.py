"""Tests for hermes_cloud.app.security.catalog_signing (v3.3.2c-1)."""
from __future__ import annotations

import base64
import os

import pytest

from hermes_cloud.app.security import catalog_signing as cs


def _b64(seed: bytes) -> str:
    return base64.b64encode(seed).decode("ascii")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip every signing-related env var before each test."""
    monkeypatch.delenv(cs.ENV_CURRENT, raising=False)
    for n in range(1, cs.MAX_HISTORICAL_KEYS + 1):
        monkeypatch.delenv(cs.ENV_PREV_PATTERN.format(n=n), raising=False)
    yield


class TestDeriveKeyId:
    def test_stable(self):
        k = b"\x01" * 32
        assert cs.derive_key_id(k) == cs.derive_key_id(k)

    def test_different_keys_different_ids(self):
        assert cs.derive_key_id(b"\x01" * 32) != cs.derive_key_id(b"\x02" * 32)

    def test_format(self):
        kid = cs.derive_key_id(b"\x01" * 32)
        assert len(kid) == 8
        assert all(c in "0123456789abcdef" for c in kid)


class TestSignVerifyRoundtrip:
    def test_roundtrip(self, monkeypatch):
        key = b"\x07" * 32
        monkeypatch.setenv(cs.ENV_CURRENT, _b64(key))
        kid, k = cs.get_current_signing_key()
        sig = cs.sign_spec("deadbeef", k)
        assert cs.verify_spec_signature("deadbeef", sig, kid) is True

    def test_signature_changes_with_payload(self, monkeypatch):
        key = b"\x09" * 32
        monkeypatch.setenv(cs.ENV_CURRENT, _b64(key))
        _, k = cs.get_current_signing_key()
        assert cs.sign_spec("aaa", k) != cs.sign_spec("bbb", k)


class TestHistoricalKeys:
    def test_prev_key_verifies(self, monkeypatch):
        cur = b"\x10" * 32
        prev = b"\x20" * 32
        monkeypatch.setenv(cs.ENV_CURRENT, _b64(cur))
        monkeypatch.setenv(cs.ENV_PREV_PATTERN.format(n=1), _b64(prev))

        prev_kid = cs.derive_key_id(prev)
        sig = cs.sign_spec("hash-x", prev)
        # Verify against prev key id even though current is different.
        assert cs.verify_spec_signature("hash-x", sig, prev_kid) is True

    def test_unknown_key_id_fails(self, monkeypatch):
        cur = b"\x30" * 32
        monkeypatch.setenv(cs.ENV_CURRENT, _b64(cur))
        # Sign with a key not in env.
        rogue = b"\x40" * 32
        sig = cs.sign_spec("hash-y", rogue)
        rogue_kid = cs.derive_key_id(rogue)
        assert cs.verify_spec_signature("hash-y", sig, rogue_kid) is False

    def test_corrupt_prev_skipped(self, monkeypatch):
        cur = b"\x50" * 32
        monkeypatch.setenv(cs.ENV_CURRENT, _b64(cur))
        monkeypatch.setenv(cs.ENV_PREV_PATTERN.format(n=1), "!!not-base64!!")
        prev = b"\x60" * 32
        monkeypatch.setenv(cs.ENV_PREV_PATTERN.format(n=2), _b64(prev))
        # PREV_1 is corrupt → skipped; PREV_2 is fine.
        prev_kid = cs.derive_key_id(prev)
        sig = cs.sign_spec("zz", prev)
        assert cs.verify_spec_signature("zz", sig, prev_kid) is True

    def test_max_three_historical(self, monkeypatch):
        cur = b"\x70" * 32
        monkeypatch.setenv(cs.ENV_CURRENT, _b64(cur))
        for n in range(1, 4):
            monkeypatch.setenv(
                cs.ENV_PREV_PATTERN.format(n=n), _b64(bytes([0x80 + n]) * 32),
            )
        keys = cs.get_historical_signing_keys()
        assert len(keys) == 3


class TestFailFast:
    def test_missing_current_key_raises(self):
        with pytest.raises(RuntimeError) as exc_info:
            cs.get_current_signing_key()
        assert cs.ENV_CURRENT in str(exc_info.value)

    def test_invalid_base64_current_raises(self, monkeypatch):
        monkeypatch.setenv(cs.ENV_CURRENT, "!!!not base64!!!")
        with pytest.raises(ValueError):
            cs.get_current_signing_key()

    def test_verify_returns_false_when_current_missing(self):
        # No current → verify cannot trust anything; returns False rather
        # than crashing bootstrap.
        assert cs.verify_spec_signature("h", "abc", "deadbeef") is False

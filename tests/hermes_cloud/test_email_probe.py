"""Tests for the email channel probe (channel_registry._probe_email).

The probe wraps imaplib / smtplib in a thread executor and reports
per-leg errors back to the SPA. We test with stubbed IMAP/SMTP classes
so the suite stays offline and deterministic.
"""
from __future__ import annotations

import asyncio
import imaplib
import smtplib
from pathlib import Path
from typing import Any, Dict

import pytest

from hermes_cloud.app.channel_registry import _probe_email


# ---------------------------------------------------------------------------
# Helpers — stub IMAP/SMTP classes installed via monkeypatch.
# ---------------------------------------------------------------------------


class _FakeIMAP:
    """imaplib.IMAP4_SSL stand-in. Construct with ``fail=...`` to make
    ``login`` raise that imaplib error; otherwise login succeeds."""

    instances: list["_FakeIMAP"] = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.logged_out = False
        self._fail: BaseException | None = None
        _FakeIMAP.instances.append(self)

    def login(self, user, password):
        if self._fail is not None:
            raise self._fail
        return ("OK", [b""])

    def logout(self):
        self.logged_out = True
        return ("BYE", [b""])


class _FakeSMTP:
    """smtplib.SMTP_SSL / SMTP stand-in. Same pattern as _FakeIMAP."""

    instances: list["_FakeSMTP"] = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host = host
        self.port = port
        self.quit_called = False
        self._login_fail: BaseException | None = None
        self.starttls_called = False
        _FakeSMTP.instances.append(self)

    def ehlo(self):
        return (250, b"hi")

    def starttls(self, context=None):
        self.starttls_called = True
        return (220, b"go ahead")

    def login(self, user, password):
        if self._login_fail is not None:
            raise self._login_fail
        return (235, b"ok")

    def quit(self):
        self.quit_called = True


@pytest.fixture(autouse=True)
def _reset_fakes():
    _FakeIMAP.instances.clear()
    _FakeSMTP.instances.clear()
    yield
    _FakeIMAP.instances.clear()
    _FakeSMTP.instances.clear()


@pytest.fixture
def patch_net(monkeypatch):
    """Default: both IMAP and SMTP succeed. Tests tweak instances after."""
    monkeypatch.setattr(imaplib, "IMAP4_SSL", _FakeIMAP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    return monkeypatch


def _run(extra: Dict[str, Any]):
    return asyncio.get_event_loop().run_until_complete(
        _probe_email(extra, Path("/tmp/nonexistent-profile"))
    ) if False else asyncio.run(_probe_email(extra, Path("/tmp/nonexistent-profile")))


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_imap_and_smtp_both_succeed(patch_net):
    result = _run({
        "address": "bot@example.com",
        "password": "pw",
        "imap_host": "imap.example.com",
        "smtp_host": "smtp.example.com",
        "smtp_port": "465",
    })
    assert result.ok is True
    assert result.error == ""
    assert result.bot_name == "bot@example.com"
    # both legs ran, both cleaned up.
    assert len(_FakeIMAP.instances) == 1
    assert _FakeIMAP.instances[0].logged_out is True
    assert len(_FakeSMTP.instances) == 1
    assert _FakeSMTP.instances[0].quit_called is True
    # 465 → implicit SSL, no STARTTLS.
    assert _FakeSMTP.instances[0].starttls_called is False


def test_smtp_port_587_uses_starttls(patch_net):
    _run({
        "address": "bot@example.com", "password": "pw",
        "imap_host": "imap.example.com",
        "smtp_host": "smtp.example.com", "smtp_port": "587",
    })
    assert _FakeSMTP.instances[0].starttls_called is True


def test_smtp_skipped_when_host_blank(patch_net):
    """Receive-only setups (no SMTP) are valid — probe shouldn't fail."""
    result = _run({
        "address": "bot@example.com", "password": "pw",
        "imap_host": "imap.example.com",
    })
    assert result.ok is True
    assert len(_FakeSMTP.instances) == 0


# ---------------------------------------------------------------------------
# Failure surfaces — error string carries the server's raw rejection.
# ---------------------------------------------------------------------------


def test_imap_login_failure_decodes_server_bytes(monkeypatch):
    """Tencent Exmail returns bytes in IMAP4.error.args — we must decode
    them or the SPA shows ``b'...'`` literals."""
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)

    class _BadIMAP(_FakeIMAP):
        def login(self, user, password):
            raise imaplib.IMAP4.error(
                b"Login fail. Account is abnormal, service is not open, "
                b"password is incorrect, login frequency limited, "
                b"or system is busy."
            )
    monkeypatch.setattr(imaplib, "IMAP4_SSL", _BadIMAP)

    result = _run({
        "address": "bot@example.com", "password": "pw",
        "imap_host": "imap.exmail.qq.com",
        "smtp_host": "smtp.exmail.qq.com", "smtp_port": "465",
    })
    assert result.ok is False
    assert "IMAP:" in result.error
    assert "Login fail" in result.error
    # decoded, not raw bytes repr.
    assert "b'" not in result.error


def test_smtp_auth_failure_includes_code_and_text(monkeypatch):
    monkeypatch.setattr(imaplib, "IMAP4_SSL", _FakeIMAP)

    class _BadSMTP(_FakeSMTP):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(
                535, b"authentication failed, system busy"
            )
    monkeypatch.setattr(smtplib, "SMTP_SSL", _BadSMTP)
    monkeypatch.setattr(smtplib, "SMTP", _BadSMTP)

    result = _run({
        "address": "bot@example.com", "password": "pw",
        "imap_host": "imap.example.com",
        "smtp_host": "smtp.example.com", "smtp_port": "465",
    })
    assert result.ok is False
    assert "SMTP:" in result.error
    assert "535" in result.error
    assert "authentication failed" in result.error
    assert "IMAP:" not in result.error  # IMAP succeeded — only SMTP reported.


def test_both_legs_fail_reports_both(monkeypatch):
    class _BadIMAP(_FakeIMAP):
        def login(self, u, p):
            raise imaplib.IMAP4.error(b"imap-no")

    class _BadSMTP(_FakeSMTP):
        def login(self, u, p):
            raise smtplib.SMTPAuthenticationError(535, b"smtp-no")
    monkeypatch.setattr(imaplib, "IMAP4_SSL", _BadIMAP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _BadSMTP)
    monkeypatch.setattr(smtplib, "SMTP", _BadSMTP)

    result = _run({
        "address": "bot@example.com", "password": "pw",
        "imap_host": "imap.example.com",
        "smtp_host": "smtp.example.com", "smtp_port": "465",
    })
    assert result.ok is False
    assert "IMAP:" in result.error
    assert "imap-no" in result.error
    assert "SMTP:" in result.error
    assert "smtp-no" in result.error


# ---------------------------------------------------------------------------
# Config validation — bad inputs reported without touching the network.
# ---------------------------------------------------------------------------


def test_missing_required_fields_short_circuits(patch_net):
    result = _run({"address": "bot@example.com", "password": "pw"})  # no imap_host
    assert result.ok is False
    assert "missing required" in result.error
    assert _FakeIMAP.instances == []  # never dialed.


def test_invalid_imap_port_short_circuits(patch_net):
    result = _run({
        "address": "bot@example.com", "password": "pw",
        "imap_host": "imap.example.com", "imap_port": "not-a-port",
    })
    assert result.ok is False
    assert "invalid imap_port" in result.error
    assert _FakeIMAP.instances == []


def test_invalid_smtp_port_short_circuits(patch_net):
    result = _run({
        "address": "bot@example.com", "password": "pw",
        "imap_host": "imap.example.com",
        "smtp_host": "smtp.example.com", "smtp_port": "foo",
    })
    assert result.ok is False
    assert "invalid smtp_port" in result.error

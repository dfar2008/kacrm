"""Tests for the email.send gateway executor."""
from __future__ import annotations

import pytest


def test_execute_missing_to():
    from hermes.action_gateway.executors.email_send import execute

    with pytest.raises(ValueError, match="'to' is required"):
        execute({"params": {"subject": "hi", "body": "hello"}}, None)


def test_execute_missing_subject():
    from hermes.action_gateway.executors.email_send import execute

    with pytest.raises(ValueError, match="'subject' is required"):
        execute({"params": {"to": "a@b.com", "body": "hello"}}, None)


def test_execute_no_smtp_configured(monkeypatch):
    from hermes.action_gateway.executors.email_send import execute

    monkeypatch.delenv("HERMES_SMTP_HOST", raising=False)
    with pytest.raises(RuntimeError, match="HERMES_SMTP_HOST"):
        execute(
            {"params": {"to": "a@b.com", "subject": "hi", "body": "hello"}},
            None,
        )


def test_execute_sends_via_smtp(monkeypatch):
    """Verify execute builds message and calls SMTP."""
    from hermes.action_gateway.executors import email_send

    monkeypatch.setenv("HERMES_SMTP_HOST", "mail.test.com")
    monkeypatch.setenv("HERMES_SMTP_PORT", "587")
    monkeypatch.setenv("HERMES_SMTP_USER", "user@test.com")
    monkeypatch.setenv("HERMES_SMTP_PASS", "secret")
    monkeypatch.setenv("HERMES_SMTP_FROM", "noreply@test.com")
    monkeypatch.setenv("HERMES_SMTP_TLS", "false")

    sent_messages = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=30):
            self.host = host
            self.port = port

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self):
            pass

        def login(self, user, pw):
            pass

        def send_message(self, msg):
            sent_messages.append(msg)

    monkeypatch.setattr(email_send, "smtplib", type("mod", (), {"SMTP": FakeSMTP}))

    result = email_send.execute(
        {
            "params": {
                "to": ["alice@test.com", "bob@test.com"],
                "subject": "Test Email",
                "body": "Hello from agent",
            },
        },
        None,
    )

    assert len(sent_messages) == 1
    msg = sent_messages[0]
    assert msg["To"] == "alice@test.com, bob@test.com"
    assert msg["Subject"] == "Test Email"
    assert msg["From"] == "noreply@test.com"
    assert "revert_token" in result
    assert result["revert_token"]["op"] == "email_recall"
    assert result["revert_token"]["to"] == ["alice@test.com", "bob@test.com"]


def test_revert_logs_warning(caplog):
    """Revert should log a warning since SMTP recall isn't supported."""
    from hermes.action_gateway.executors.email_send import revert

    import logging

    with caplog.at_level(logging.WARNING):
        revert(
            {
                "message_id": "<test-123@mail.com>",
                "to": ["a@b.com"],
                "subject": "Test",
            },
            None,
        )

    assert "email_recall" in caplog.text


def test_executor_registered():
    """email.send executor should be registered in the gateway."""
    from hermes.action_gateway.dispatcher import _get_executor

    executor = _get_executor("email.send")
    assert executor is not None
    assert hasattr(executor, "revert")

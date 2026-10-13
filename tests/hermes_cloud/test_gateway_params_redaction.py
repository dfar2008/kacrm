"""Gateway params PII redaction (charter 定律 6).

The dashboard renders ``gateway_actions.params`` verbatim from the
list/detail endpoints. The agent often stashes the raw incoming text
in params (customer email, phone, ID from WeChat / email ingestion).
Charter 定律 6 says PII must not leave the system boundary unmasked —
even into operator-facing APIs.

These tests pin the redactor contract directly; router-level tests
would need full dashboard auth + DB which is integration territory.
"""
from __future__ import annotations

from hermes_cloud.app.gateway.api import (
    _redact_action_row,
    _redact_params,
    _redact_string,
)


def test_redact_email_phone_id():
    s = "mail a@b.com call 13812345678 id 110105199003071234"
    r = _redact_string(s)
    assert "a@b.com" not in r
    assert "13812345678" not in r
    assert "110105199003071234" not in r
    assert "[EMAIL]" in r
    assert "[PHONE]" in r
    assert "[ID]" in r


def test_redact_params_walks_nested_structures():
    params = {
        "op": "create",
        "data": {
            "name": "Alice",
            "email": "alice@acme.com",
            "phones": ["13812345678", "not-a-phone"],
        },
        "meta": ("x@y.co", 42),
    }
    out = _redact_params(params)
    assert out["op"] == "create"
    assert out["data"]["name"] == "Alice"
    assert out["data"]["email"] == "[EMAIL]"
    assert out["data"]["phones"][0] == "[PHONE]"
    assert out["data"]["phones"][1] == "not-a-phone"
    assert out["meta"][0] == "[EMAIL]"
    assert out["meta"][1] == 42


def test_redact_action_row_masks_params_field_only():
    row = {
        "id": "abc",
        "agent_name": "field_extractor",
        "state": "pending_approval",
        "params": {"email": "x@y.com"},
    }
    out = _redact_action_row(row)
    assert out["id"] == "abc"
    assert out["agent_name"] == "field_extractor"
    assert out["params"]["email"] == "[EMAIL]"
    # Original not mutated
    assert row["params"]["email"] == "x@y.com"


def test_redact_action_row_handles_missing_params():
    row = {"id": "abc", "state": "executed"}
    out = _redact_action_row(row)
    assert out == row


def test_redact_action_row_handles_none():
    assert _redact_action_row(None) is None


def test_redact_empty_string_roundtrips():
    assert _redact_string("") == ""


def test_redact_preserves_non_string_leaves():
    params = {"count": 5, "flag": True, "ratio": 0.7, "nothing": None}
    out = _redact_params(params)
    assert out == params

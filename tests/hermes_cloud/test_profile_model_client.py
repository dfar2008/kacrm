from __future__ import annotations

from hermes_cloud.app.llm.profile_model_client import resolve_profile_default_model


class _FakeDb:
    def __init__(self, profile_row=None, user_row=None):
        self._profile_row = profile_row
        self._user_row = user_row

    def fetchone(self, sql, params):
        if "FROM profile_models" in sql:
            return self._profile_row
        if "FROM user_models" in sql:
            return self._user_row
        return None


def test_resolve_profile_model_prefers_profile_row():
    db = _FakeDb(
        profile_row={"model": "gpt-4o-mini", "base_url": "https://api.example.com", "api_key": "k1"},
        user_row={"model": "gpt-4.1", "base_url": "https://api2.example.com", "api_key": "k2"},
    )
    cfg = resolve_profile_default_model(user_id=1, profile_id="p1", db=db)
    assert cfg is not None
    assert cfg.model == "gpt-4o-mini"
    assert cfg.source == "profile"


def test_resolve_profile_model_falls_back_to_user_default():
    db = _FakeDb(
        profile_row={"model": None, "base_url": None, "api_key": None},
        user_row={"model": "gpt-4.1", "base_url": "https://api2.example.com", "api_key": "k2"},
    )
    cfg = resolve_profile_default_model(user_id=1, profile_id="p1", db=db)
    assert cfg is not None
    assert cfg.model == "gpt-4.1"
    assert cfg.source == "user_default"


def test_resolve_profile_model_rejects_trained_adapter_name():
    db = _FakeDb(
        profile_row={"model": "crm_grpo_v13", "base_url": "https://api.example.com", "api_key": "k1"},
        user_row=None,
    )
    cfg = resolve_profile_default_model(user_id=1, profile_id="p1", db=db)
    assert cfg is None

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.main import create_app

# ``profile_store`` is PostgreSQL-only since the InMemoryProfileStore was
# dropped (commit 367e344). The model-config endpoints resolve a profile
# via ``app.state.profile_store``, wired by ``create_app`` only when
# ``HERMES_CRM_DATABASE_URL`` reaches a real PG.
pytestmark = pytest.mark.requires_real_pg


def test_put_user_model_syncs_public_key_for_opencode_free_provider(tmp_path):
    app = create_app(
        AppConfig(
            api_keys={"sk-test": "user-1"},
            hermes_home_root=str(tmp_path),
        )
    )
    client = TestClient(app)

    resp = client.put(
        "/api/user-config/model",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "providers_json": {
                "default_model": "opencode-zen::big-pickle",
                "providers": {
                    "opencode-zen": {
                        "enabled": True,
                        "api_key": "",
                        "base_url": "https://opencode.ai/zen/v1",
                        "api_format": "openai",
                        "models": [{"id": "big-pickle", "name": "Big Pickle"}],
                    }
                },
            }
        },
    )

    assert resp.status_code == 200

    config_path = tmp_path / "profiles" / "user-1" / "config.yaml"
    saved = yaml.safe_load(config_path.read_text()) or {}

    assert saved["model"]["api_key"] == "public"
    assert saved["model"]["default"] == "big-pickle"
    assert saved["model"]["model"] == "big-pickle"
    assert saved["custom_providers"][0]["api_key"] == "public"


def test_put_user_model_writes_spa_provider_id_not_custom_fallback(tmp_path):
    """Regression: when the SPA picks MiniMax (or any non-opencode provider),
    the synced ``model.provider`` must be the provider id from
    providers_json (``minimax``), not the URL-fallback ``custom``.

    Writing ``custom`` makes hermes' auxiliary client route through the
    OpenAI SDK with a ``/anthropic`` → ``/v1`` path rewrite, which 404s
    against MiniMax-CN (no OpenAI surface at /v1/chat/completions on that
    host). The fix is to use the SPA-supplied provider id as the source
    of truth.
    """
    app = create_app(
        AppConfig(
            api_keys={"sk-test": "user-1"},
            hermes_home_root=str(tmp_path),
        )
    )
    client = TestClient(app)

    resp = client.put(
        "/api/user-config/model",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "providers_json": {
                "default_model": "minimax::MiniMax-M2.7",
                "providers": {
                    "minimax": {
                        "enabled": True,
                        "api_key": "sk-mm-test",
                        "base_url": "https://api.minimaxi.com/anthropic",
                        "api_format": "anthropic",
                        "models": [{"id": "MiniMax-M2.7", "name": "MiniMax M2.7"}],
                    }
                },
            }
        },
    )

    assert resp.status_code == 200

    config_path = tmp_path / "profiles" / "user-1" / "config.yaml"
    saved = yaml.safe_load(config_path.read_text()) or {}

    # ``custom:<id>`` prefix — bypasses hermes'
    # ``_get_named_custom_provider`` guard at runtime_provider.py:432
    # which would otherwise hand canonical built-in names (minimax,
    # deepseek, openai, anthropic, ...) to the env-var resolver and
    # ignore the inline api_key in custom_providers. Cloud has no env
    # vars; the SPA-supplied key is the source of truth.
    assert saved["model"]["provider"] == "custom:minimax"
    assert saved["model"]["api_mode"] == "anthropic_messages"
    assert saved["model"]["base_url"] == "https://api.minimaxi.com/anthropic"
    # The named custom_providers entry is matched by both ``minimax`` and
    # ``custom:minimax`` per runtime_provider.py:452, so the entry name
    # stays as the bare provider id.
    cp = {p["name"]: p for p in saved.get("custom_providers", [])}
    assert "minimax" in cp
    assert cp["minimax"]["api_mode"] == "anthropic_messages"
    # ``models``: list of model ids enabled for this provider — read by
    # hermes' api_server platform to resolve per-request body.model
    # overrides (SPA ModelPicker) back to this entry's credentials.
    assert cp["minimax"]["models"] == ["MiniMax-M2.7"]


def test_put_user_model_syncs_models_list_for_all_enabled_providers(tmp_path):
    """Regression: every enabled provider in providers_json must land in
    custom_providers with its ``models`` list intact. Without this, the
    hermes api_server can't switch the agent's model+credentials when
    the SPA ModelPicker sends body.model for a non-default provider —
    every request falls back to the gateway default silently.
    """
    app = create_app(
        AppConfig(
            api_keys={"sk-test": "user-1"},
            hermes_home_root=str(tmp_path),
        )
    )
    client = TestClient(app)

    resp = client.put(
        "/api/user-config/model",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "providers_json": {
                "default_model": "minimax::MiniMax-M2.7",
                "providers": {
                    "minimax": {
                        "enabled": True,
                        "api_key": "sk-mm",
                        "base_url": "https://api.minimaxi.com/anthropic",
                        "api_format": "anthropic",
                        "models": [{"id": "MiniMax-M2.7", "name": "MiniMax M2.7"}],
                    },
                    "deepseek": {
                        "enabled": True,
                        "api_key": "sk-ds",
                        "base_url": "https://api.deepseek.com",
                        "api_format": "openai",
                        "models": [
                            {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro"},
                            {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash"},
                        ],
                    },
                    "moonshot": {
                        # Disabled — must NOT appear in custom_providers.
                        "enabled": False,
                        "api_key": "",
                        "base_url": "https://api.moonshot.cn/v1",
                        "api_format": "openai",
                        "models": [{"id": "moonshot-v1-8k", "name": "Moonshot V1 8K"}],
                    },
                },
            }
        },
    )

    assert resp.status_code == 200

    config_path = tmp_path / "profiles" / "user-1" / "config.yaml"
    saved = yaml.safe_load(config_path.read_text()) or {}
    cp = {p["name"]: p for p in saved.get("custom_providers", [])}

    assert set(cp.keys()) == {"minimax", "deepseek"}, (
        "disabled providers must not appear in custom_providers"
    )
    assert cp["deepseek"]["models"] == ["deepseek-v4-pro", "deepseek-v4-flash"]
    assert cp["minimax"]["models"] == ["MiniMax-M2.7"]


def test_put_user_model_unknown_endpoint_falls_back_to_custom(tmp_path):
    """When no providers_json entry matches the base_url (e.g. a raw
    base_url manually pasted with no provider radio), keep the legacy
    ``custom`` fallback so existing self-hosted endpoints still work.
    """
    app = create_app(
        AppConfig(
            api_keys={"sk-test": "user-1"},
            hermes_home_root=str(tmp_path),
        )
    )
    client = TestClient(app)

    resp = client.put(
        "/api/user-config/model",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "my-local-model",
            "base_url": "https://my-private-llm.example.com/v1",
            "api_key": "sk-local",
        },
    )

    assert resp.status_code == 200

    config_path = tmp_path / "profiles" / "user-1" / "config.yaml"
    saved = yaml.safe_load(config_path.read_text()) or {}

    assert saved["model"]["provider"] == "custom"


def test_put_user_model_derives_auxiliary_and_moa(tmp_path):
    """两个国产 provider(含 deepseek-flash)→ config.yaml 派生 auxiliary(指向 flash)
    与 moa.presets.deep-council。"""
    app = create_app(
        AppConfig(api_keys={"sk-test": "user-1"}, hermes_home_root=str(tmp_path))
    )
    client = TestClient(app)
    resp = client.put(
        "/api/user-config/model",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "providers_json": {
                "default_model": "minimax::MiniMax-M3",
                "providers": {
                    "minimax": {
                        "enabled": True, "api_key": "k", "base_url": "https://api.minimaxi.com/anthropic",
                        "api_format": "anthropic", "models": [{"id": "MiniMax-M3", "name": "MiniMax M3"}],
                    },
                    "opencode-zen": {
                        "enabled": True, "api_key": "", "base_url": "https://opencode.ai/zen/v1",
                        "api_format": "openai",
                        "models": [
                            {"id": "deepseek-v4-flash-free", "name": "DeepSeek V4 Flash Free"},
                            {"id": "glm-5.1", "name": "GLM-5.1"},
                        ],
                    },
                },
            }
        },
    )
    assert resp.status_code == 200
    saved = yaml.safe_load((tmp_path / "profiles" / "user-1" / "config.yaml").read_text()) or {}
    assert saved["auxiliary"]["compression"]["model"] == "deepseek-v4-flash-free"
    assert "deep-council" in saved["moa"]["presets"]


def test_put_user_model_moa_disabled_removes_deep_council(tmp_path):
    app = create_app(
        AppConfig(api_keys={"sk-test": "user-1"}, hermes_home_root=str(tmp_path))
    )
    client = TestClient(app)
    resp = client.put(
        "/api/user-config/model",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "providers_json": {
                "default_model": "minimax::MiniMax-M3",
                "moa": {"enabled": False},
                "providers": {
                    "minimax": {
                        "enabled": True, "api_key": "k", "base_url": "https://api.minimaxi.com/anthropic",
                        "api_format": "anthropic", "models": [{"id": "MiniMax-M3", "name": "MiniMax M3"}],
                    },
                    "opencode-zen": {
                        "enabled": True, "api_key": "", "base_url": "https://opencode.ai/zen/v1",
                        "api_format": "openai", "models": [{"id": "glm-5.1", "name": "GLM-5.1"}],
                    },
                },
            }
        },
    )
    assert resp.status_code == 200
    saved = yaml.safe_load((tmp_path / "profiles" / "user-1" / "config.yaml").read_text()) or {}
    assert "deep-council" not in saved.get("moa", {}).get("presets", {})

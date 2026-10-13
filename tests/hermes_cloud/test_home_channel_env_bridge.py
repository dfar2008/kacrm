"""结构化 home_channel → *_HOME_CHANNEL env 投影（云端桥接）。"""
from __future__ import annotations

from hermes_cloud.app.profile_manager import _home_channel_env_overrides


def test_projects_feishu_and_weixin_home_channels():
    platforms = {
        "feishu": {
            "enabled": True,
            "extra": {"app_id": "cli_x"},
            "home_channel": {"platform": "feishu", "chat_id": "ou_83eb", "name": "Home"},
        },
        "weixin": {
            "enabled": True,
            "extra": {"account_id": "f974@im.bot", "token": "t"},
            "home_channel": {"platform": "weixin", "chat_id": "o9cq@im.wechat", "name": "Home"},
        },
    }
    assert _home_channel_env_overrides(platforms) == {
        "FEISHU_HOME_CHANNEL": "ou_83eb",
        "WEIXIN_HOME_CHANNEL": "o9cq@im.wechat",
    }


def test_uses_hermes_nonstandard_env_names():
    platforms = {
        "matrix": {"home_channel": {"chat_id": "!room:hs"}},
        "email": {"home_channel": {"chat_id": "boss@corp.com"}},
    }
    out = _home_channel_env_overrides(platforms)
    assert out["MATRIX_HOME_ROOM"] == "!room:hs"
    assert out["EMAIL_HOME_ADDRESS"] == "boss@corp.com"


def test_projects_thread_id_when_present():
    platforms = {
        "telegram": {"home_channel": {"chat_id": "-100123", "thread_id": "17"}},
    }
    out = _home_channel_env_overrides(platforms)
    assert out["TELEGRAM_HOME_CHANNEL"] == "-100123"
    assert out["TELEGRAM_HOME_CHANNEL_THREAD_ID"] == "17"


def test_skips_channel_without_home_channel_or_chat_id():
    platforms = {
        "feishu": {"extra": {"app_id": "cli_x"}},
        "weixin": {"home_channel": {"chat_id": ""}},
        "slack": {"home_channel": "not-a-dict"},
    }
    assert _home_channel_env_overrides(platforms) == {}


def test_returns_empty_for_non_dict_input():
    assert _home_channel_env_overrides(None) == {}
    assert _home_channel_env_overrides("nope") == {}
    assert _home_channel_env_overrides({}) == {}


from pathlib import Path

import yaml

from hermes_cloud.app.profile_manager import home_channel_env_for_profile


def test_reads_profile_config_and_projects(tmp_path: Path):
    profile_dir = tmp_path / "profiles" / "admin"
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text(
        yaml.dump(
            {
                "platforms": {
                    "feishu": {
                        "enabled": True,
                        "extra": {"app_id": "cli_x"},
                        "home_channel": {"platform": "feishu", "chat_id": "ou_83eb"},
                    },
                }
            },
            allow_unicode=True,
        )
    )
    assert home_channel_env_for_profile(profile_dir) == {"FEISHU_HOME_CHANNEL": "ou_83eb"}


def test_returns_empty_when_no_config(tmp_path: Path):
    profile_dir = tmp_path / "profiles" / "empty"
    profile_dir.mkdir(parents=True)
    assert home_channel_env_for_profile(profile_dir) == {}


from hermes_cloud.app.profile_manager import _scrub_legacy_channel_residue


def test_scrub_removes_top_level_dead_scalars_and_extra_user_id(tmp_path):
    cfg = {
        "FEISHU_HOME_CHANNEL": "ou_old",
        "WEIXIN_HOME_CHANNEL": "o9cq@im.wechat",
        "WECHAT_HOME_CHANNEL": "x@im.bot",
        "WEIXIN_TOKEN": "legacy",
        "model": {"provider": "anthropic"},
        "platforms": {
            "weixin": {"enabled": True, "extra": {"account_id": "f974@im.bot", "token": "t", "user_id": "o9cq@im.wechat"}},
        },
    }
    changed, out = _scrub_legacy_channel_residue(cfg)
    assert changed is True
    assert "FEISHU_HOME_CHANNEL" not in out
    assert "WEIXIN_HOME_CHANNEL" not in out
    assert "WECHAT_HOME_CHANNEL" not in out
    assert "WEIXIN_TOKEN" not in out
    assert out["model"] == {"provider": "anthropic"}
    assert "user_id" not in out["platforms"]["weixin"]["extra"]
    assert out["platforms"]["weixin"]["extra"]["token"] == "t"


def test_scrub_is_idempotent(tmp_path):
    cfg = {"model": {"provider": "anthropic"}, "platforms": {"feishu": {"extra": {"app_id": "x"}}}}
    changed, out = _scrub_legacy_channel_residue(cfg)
    assert changed is False
    assert out == cfg

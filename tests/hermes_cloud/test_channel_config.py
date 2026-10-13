"""Per-profile channel credentials storage (config.yaml)."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
import yaml

from hermes_cloud.app.channel_config import (
    read_channel_config,
    read_profile_config,
    write_channel_config,
    write_profile_config,
)


@pytest.fixture
def profile_dir(tmp_path: Path) -> Path:
    return tmp_path / "profiles" / "test-user"


# ---------------------------------------------------------------------------
# read_channel_config — defensive shape handling
# ---------------------------------------------------------------------------


def test_read_returns_empty_when_no_config(profile_dir):
    assert read_channel_config(profile_dir, "feishu") == {}


def test_read_returns_empty_when_no_platforms_section(profile_dir):
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text("model: {provider: anthropic}\n")
    assert read_channel_config(profile_dir, "feishu") == {}


def test_read_returns_empty_when_channel_not_configured(profile_dir):
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text(
        "platforms:\n"
        "  telegram:\n"
        "    enabled: true\n"
        "    extra: {token: abc}\n",
    )
    assert read_channel_config(profile_dir, "feishu") == {}


def test_read_returns_extra_dict(profile_dir):
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text(
        "platforms:\n"
        "  feishu:\n"
        "    enabled: true\n"
        "    extra:\n"
        "      app_id: cli_xyz\n"
        "      app_secret: secret_xyz\n"
        "      domain: feishu\n",
    )
    extra = read_channel_config(profile_dir, "feishu")
    assert extra == {
        "app_id": "cli_xyz",
        "app_secret": "secret_xyz",
        "domain": "feishu",
    }


def test_read_tolerates_malformed_yaml(profile_dir):
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text("not: valid: yaml: at: all: [")
    assert read_channel_config(profile_dir, "feishu") == {}


def test_read_tolerates_platforms_being_a_string(profile_dir):
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text("platforms: this-should-be-a-dict\n")
    assert read_channel_config(profile_dir, "feishu") == {}


def test_read_tolerates_channel_entry_being_a_string(profile_dir):
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text(
        "platforms:\n  feishu: 'oops'\n"
    )
    assert read_channel_config(profile_dir, "feishu") == {}


def test_read_tolerates_extra_being_a_string(profile_dir):
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text(
        "platforms:\n  feishu:\n    enabled: true\n    extra: 'oops'\n"
    )
    assert read_channel_config(profile_dir, "feishu") == {}


# ---------------------------------------------------------------------------
# write_channel_config — surgical updates
# ---------------------------------------------------------------------------


def test_write_creates_platforms_section_from_scratch(profile_dir):
    write_channel_config(profile_dir, "feishu", {
        "app_id": "cli_xyz", "app_secret": "s", "domain": "feishu",
    })
    assert read_channel_config(profile_dir, "feishu") == {
        "app_id": "cli_xyz", "app_secret": "s", "domain": "feishu",
    }


def test_write_sets_enabled_true(profile_dir):
    write_channel_config(profile_dir, "telegram", {"token": "123:abc"})
    data = read_profile_config(profile_dir)
    assert data["platforms"]["telegram"]["enabled"] is True


def test_write_empty_extra_deletes_channel_entry(profile_dir):
    write_channel_config(profile_dir, "feishu", {"app_id": "x", "app_secret": "y"})
    assert "feishu" in read_profile_config(profile_dir)["platforms"]
    write_channel_config(profile_dir, "feishu", {})
    platforms = read_profile_config(profile_dir).get("platforms", {})
    assert "feishu" not in platforms


def test_write_preserves_other_channels(profile_dir):
    write_channel_config(profile_dir, "feishu", {"app_id": "x", "app_secret": "y"})
    write_channel_config(profile_dir, "telegram", {"token": "t"})
    write_channel_config(profile_dir, "feishu", {
        "app_id": "x2", "app_secret": "y2",
    })
    assert read_channel_config(profile_dir, "feishu")["app_id"] == "x2"
    assert read_channel_config(profile_dir, "telegram") == {"token": "t"}


def test_write_preserves_top_level_keys(profile_dir):
    # Pre-seed with non-channel keys (model section, system_prompt)
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text(
        "model:\n  provider: custom\n  base_url: https://api.example.com\n"
        "agent:\n  system_prompt: 你是 Hermes\n",
    )
    write_channel_config(profile_dir, "feishu", {"app_id": "x", "app_secret": "y"})
    data = read_profile_config(profile_dir)
    assert data["model"]["provider"] == "custom"
    assert data["model"]["base_url"] == "https://api.example.com"
    assert data["agent"]["system_prompt"] == "你是 Hermes"
    assert data["platforms"]["feishu"]["extra"]["app_id"] == "x"


def test_write_deep_copies_extra_dict(profile_dir):
    """Caller-mutating the input dict after write must not corrupt the
    on-disk state. ``write_channel_config`` snapshots ``extra``."""
    original = {"app_id": "x", "app_secret": "y"}
    write_channel_config(profile_dir, "feishu", original)
    original["app_id"] = "MUTATED"
    assert read_channel_config(profile_dir, "feishu")["app_id"] == "x"


def test_write_round_trips_unicode_values(profile_dir):
    """System prompts and bot names contain CJK; YAML must not escape
    them (allow_unicode=True). Otherwise the on-disk file becomes a
    wall of \\u escapes."""
    write_channel_config(profile_dir, "feishu", {
        "app_id": "cli_xyz",
        "app_secret": "s",
        "bot_name": "胡经理助手",
    })
    raw = (profile_dir / "config.yaml").read_text()
    assert "胡经理助手" in raw
    assert "\\u" not in raw


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_write_sets_profile_dir_to_0700(profile_dir):
    write_channel_config(profile_dir, "feishu", {"app_id": "x", "app_secret": "y"})
    mode = stat.S_IMODE(os.stat(profile_dir).st_mode)
    assert mode == 0o700, f"profile_dir mode = {oct(mode)}"


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_write_sets_new_config_file_to_0600(profile_dir):
    write_channel_config(profile_dir, "feishu", {"app_id": "x", "app_secret": "y"})
    mode = stat.S_IMODE(os.stat(profile_dir / "config.yaml").st_mode)
    assert mode == 0o600, f"config.yaml mode = {oct(mode)}"


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_write_preserves_pre_existing_relaxed_perms(profile_dir):
    """If the user manually chmod'd the file (e.g. to read it in
    another shell), don't reset it on every write."""
    profile_dir.mkdir(parents=True)
    config_path = profile_dir / "config.yaml"
    config_path.write_text("model: {}\n")
    os.chmod(config_path, 0o644)  # user-loosened
    write_channel_config(profile_dir, "feishu", {"app_id": "x", "app_secret": "y"})
    mode = stat.S_IMODE(os.stat(config_path).st_mode)
    assert mode == 0o644, "existing file perms must not be overwritten"


# ---------------------------------------------------------------------------
# Round-trip via raw yaml — sanity check
# ---------------------------------------------------------------------------


def test_yaml_round_trip_matches_hermes_native_shape(profile_dir):
    """Hermes' GatewayConfig.from_dict reads ``platforms.<channel>``
    with ``enabled`` and ``extra`` siblings. Make sure that's the
    on-disk shape we produce."""
    write_channel_config(profile_dir, "feishu", {
        "app_id": "cli_xyz", "app_secret": "s",
        "encrypt_key": "ek", "verification_token": "vt",
        "domain": "feishu", "connection_mode": "websocket",
    })
    raw = yaml.safe_load((profile_dir / "config.yaml").read_text())
    feishu = raw["platforms"]["feishu"]
    assert feishu["enabled"] is True
    assert feishu["extra"]["app_id"] == "cli_xyz"
    assert feishu["extra"]["encrypt_key"] == "ek"
    assert feishu["extra"]["connection_mode"] == "websocket"

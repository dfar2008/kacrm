"""多-profile 场景下 config_store 的行为测试（Task 4a）。

约定（见 docs/2026-04-15-multi-profile-foundation.md）：
  - 不传 ``profile_id``、或 ``profile_id == user_id`` → 默认 profile，走老表（零破坏）。
  - 其它 ``profile_id`` → 独立的非默认 profile，存在新的 ``profile_*`` 表。
  - 两者互不污染：默认 profile 写入不会影响非默认 profile 读取，反之亦然。
"""
from __future__ import annotations

import pytest

from hermes_cloud.app.config_store import (
    InMemoryConfigStore,
    SqlAlchemyConfigStore,
)


@pytest.fixture(params=["inmemory", "sqlalchemy"])
def store(request, tmp_path):
    if request.param == "inmemory":
        return InMemoryConfigStore()
    db_path = tmp_path / "cfg.db"
    return SqlAlchemyConfigStore(f"sqlite+pysqlite:///{db_path}")


# ---------------------------------------------------------------------------
# 向后兼容：不传 profile_id 等价于传 user_id（默认 profile）
# ---------------------------------------------------------------------------

def test_get_without_profile_id_reads_default_profile(store):
    """历史调用点 ``get_user_model(user_id)`` 应该读到默认 profile 数据。"""
    store.set_user_model(
        user_id="alice",
        model="default-model",
        api_key="k",
        base_url="https://x",
    )
    # 老签名
    got = store.get_user_model("alice")
    assert got is not None
    assert got.model == "default-model"
    # 显式 profile_id=user_id 等价
    got2 = store.get_user_model("alice", profile_id="alice")
    assert got2 is not None
    assert got2.model == "default-model"


def test_set_without_profile_id_writes_default_profile(store):
    """``set_user_model(user_id, ...)`` 应该写到默认 profile（= profile_id=user_id 可读到）。"""
    store.set_user_model(
        user_id="alice",
        model="m1",
        api_key=None,
        base_url=None,
    )
    got = store.get_user_model("alice", profile_id="alice")
    assert got is not None
    assert got.model == "m1"


# ---------------------------------------------------------------------------
# 多 profile 隔离：alice 的 research profile 与默认 profile 互不干扰
# ---------------------------------------------------------------------------

def test_non_default_profile_is_isolated_from_default(store):
    store.set_user_model(
        user_id="alice",
        model="default-model",
        api_key=None,
        base_url=None,
    )
    store.set_user_model(
        user_id="alice",
        profile_id="alice-research",
        model="research-model",
        api_key="research-key",
        base_url="https://research",
    )

    # 默认 profile 不变
    default_cfg = store.get_user_model("alice")
    assert default_cfg is not None
    assert default_cfg.model == "default-model"

    # 非默认 profile 独立
    research_cfg = store.get_user_model("alice", profile_id="alice-research")
    assert research_cfg is not None
    assert research_cfg.model == "research-model"
    assert research_cfg.api_key == "research-key"


def test_non_default_profile_returns_none_when_unset(store):
    """没有为非默认 profile 设置过就读，返回 None（不要 fallback 到默认 profile）。"""
    store.set_user_model(
        user_id="alice",
        model="default-model",
        api_key=None,
        base_url=None,
    )
    got = store.get_user_model("alice", profile_id="alice-research")
    assert got is None


def test_toolsets_are_scoped_per_profile(store):
    store.set_user_toolsets(user_id="alice", toolsets=["web"])
    store.set_user_toolsets(
        user_id="alice", profile_id="alice-coding", toolsets=["file", "exec"]
    )
    assert store.get_user_toolsets("alice").toolsets == ["web"]
    assert store.get_user_toolsets(
        "alice", profile_id="alice-coding"
    ).toolsets == ["file", "exec"]

def test_agents_configs_are_scoped_per_profile(store):
    store.set_user_agents_config(
        user_id="alice",
        system_prompt="default agent",
        memory_enabled=True,
    )
    store.set_user_agents_config(
        user_id="alice",
        profile_id="alice-ops",
        system_prompt="ops agent",
        memory_enabled=False,
    )
    default_cfg = store.get_user_agents_config("alice")
    ops_cfg = store.get_user_agents_config("alice", profile_id="alice-ops")
    assert default_cfg.system_prompt == "default agent"
    assert default_cfg.memory_enabled is True
    assert ops_cfg.system_prompt == "ops agent"
    assert ops_cfg.memory_enabled is False


# ---------------------------------------------------------------------------
# SqlAlchemy 持久化：非默认 profile 数据在重开连接后仍然可读
# ---------------------------------------------------------------------------

def test_sqlalchemy_persists_non_default_profile_data(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'cfg.db'}"
    s1 = SqlAlchemyConfigStore(url)
    s1.set_user_model(
        user_id="alice",
        profile_id="alice-research",
        model="rm",
        api_key=None,
        base_url=None,
    )
    s2 = SqlAlchemyConfigStore(url)
    assert s2.get_user_model(
        "alice", profile_id="alice-research"
    ).model == "rm"

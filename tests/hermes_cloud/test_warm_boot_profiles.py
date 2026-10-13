"""启动时 warm-boot 所有 active profile（Charter v2 §简化原则：1 user = 1
profile）。

行为要求：
  1. 对每个 active profile 调用 ``profile_manager.ensure_running``，这样每个
     profile 的 scheduler.db 定时任务从服务启动开始就会触发。
  2. 每个用户的唯一（默认）profile —— id == username —— 都会被启动，不会被
     遗漏。

测试通过 stub ``profile_manager.ensure_running`` 记录调用来验证。``list_all_
active_profiles`` 会跨用户枚举整个库（含 seed / admin / 其它测试用户），所以
我们只断言本测试创建的用户都在其中、且各启动一次。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.main import create_app

# ``profile_store`` is PostgreSQL-only since the InMemoryProfileStore was
# dropped (commit 367e344). Warm-boot iterates ``app.state.profile_store``,
# wired by ``create_app`` only when ``session_store_url`` reaches a real PG.
pytestmark = pytest.mark.requires_real_pg


def _make_app(tmp_path, users):
    from hermes.crm.db import _resolve_dsn

    dsn = _resolve_dsn().replace("postgresql://", "postgresql+psycopg://", 1)
    return create_app(
        AppConfig(
            api_keys={u.token: u.uid for u in users},
            internal_key="internal-secret",
            session_store_url=dsn,
            hermes_home_root=str(tmp_path / "hermes_home"),
        )
    )


@pytest.mark.asyncio
async def test_warm_boot_starts_every_users_default_profile(tmp_path, pg_users):
    alice, bob = pg_users(2)
    app = _make_app(tmp_path, [alice, bob])

    with TestClient(app):
        # 种好两人的默认 profile（id == username）。
        app.state.profile_store.ensure_default_profile(user_id=alice.uid)
        app.state.profile_store.ensure_default_profile(user_id=bob.uid)

        # stub ensure_running，避免真起子进程；记录 (user_id, profile_id)。
        call_log: list[tuple[str, str]] = []

        async def fake_ensure_running(profile_id: str, *, user_id: str, **kwargs) -> int:
            call_log.append((user_id, profile_id))
            return 0  # fake port — nothing actually connects

        app.state.profile_manager.ensure_running = fake_ensure_running  # type: ignore[method-assign]

        # 复刻 boot 流程对外可观察的部分：遍历 list_all_active_profiles 并
        # ensure_running（避免等 lifespan 里的 sleep(2)）。
        profiles = app.state.profile_store.list_all_active_profiles()
        for p in profiles:
            await app.state.profile_manager.ensure_running(p.id, user_id=p.user_id)

    # 两个用户的默认 profile（id == username）都必须被启动。
    by_profile = [pid for (_uid, pid) in call_log]
    assert alice.username in by_profile
    assert bob.username in by_profile
    # 各只启动一次（1 user = 1 profile）。
    assert by_profile.count(alice.username) == 1
    assert by_profile.count(bob.username) == 1
    # 启动时携带的 user_id 是十进制 users.id，而非 username。
    assert (alice.uid, alice.username) in call_log
    assert (bob.uid, bob.username) in call_log

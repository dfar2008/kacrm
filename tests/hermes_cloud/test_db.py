from __future__ import annotations

from sqlalchemy.pool import QueuePool, StaticPool

from hermes_cloud.app.db import make_engine


def test_make_engine_uses_static_pool_for_memory_sqlite():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    try:
        assert isinstance(engine.pool, StaticPool)
    finally:
        engine.dispose()


def test_make_engine_respects_pool_env_for_non_memory_db(monkeypatch, tmp_path):
    db_path = tmp_path / "pool.db"
    monkeypatch.setenv("HERMES_CLOUD_DB_POOL_SIZE", "1")
    monkeypatch.setenv("HERMES_CLOUD_DB_MAX_OVERFLOW", "0")
    monkeypatch.setenv("HERMES_CLOUD_DB_POOL_TIMEOUT", "8")
    monkeypatch.setenv("HERMES_CLOUD_DB_POOL_RECYCLE", "120")

    engine = make_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        assert isinstance(engine.pool, QueuePool)
        assert engine.pool.size() == 1
        assert engine.pool._max_overflow == 0
        assert engine.pool._timeout == 8
        assert engine.pool._recycle == 120
    finally:
        engine.dispose()

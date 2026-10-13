"""Shared SQLAlchemy engine factory.

SQLite in-memory databases (``sqlite+pysqlite://``) require a StaticPool so
that all sessions share a single connection — otherwise each new connection
creates a brand-new empty database and tables created by ``create_all()``
disappear between requests.

For all other URLs (PostgreSQL, file-based SQLite, …) a regular engine with
the default connection pool is used.
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


def make_engine(database_url: str):
    """Return a SQLAlchemy engine suitable for the given URL."""
    is_memory = database_url in ("sqlite+pysqlite://", "sqlite://") or ":memory:" in database_url
    if is_memory:
        return create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    pool_size = int(os.environ.get("HERMES_CLOUD_DB_POOL_SIZE", "5"))
    max_overflow = int(os.environ.get("HERMES_CLOUD_DB_MAX_OVERFLOW", "10"))
    pool_timeout = int(os.environ.get("HERMES_CLOUD_DB_POOL_TIMEOUT", "30"))
    pool_recycle = int(os.environ.get("HERMES_CLOUD_DB_POOL_RECYCLE", "-1"))
    return create_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_recycle=pool_recycle,
        pool_pre_ping=True,
    )

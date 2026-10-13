"""Alembic environment for the Hermes CRM golden schema.

This is intentionally minimal: we drive migrations with raw SQL in each
revision (no SQLAlchemy metadata autogenerate), so ``env.py`` only needs to
resolve the database URL and run the migration context.
"""
from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool


# Load DB URL (and any other env) from the repo-root .env before reading
# os.environ — alembic is often invoked from shells that don't source .env.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if _ENV_PATH.is_file():
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_PATH, override=False)
    except ImportError:
        # Minimal fallback parser so alembic works without python-dotenv.
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


config = context.config


def _resolve_database_url() -> str:
    """Pick the CRM database URL from env (loaded from .env at import time).

    Priority: ALEMBIC_DATABASE_URL > HERMES_CRM_DATABASE_URL >
    CRM_DATABASE_URL > alembic.ini.

    ALEMBIC_DATABASE_URL takes precedence so migrations connect *directly*
    to PostgreSQL while the application's HERMES_CRM_DATABASE_URL points at
    pgbouncer. pgbouncer's transaction pooling reassigns server connections
    per-transaction, which is unsafe for DDL and the session-level advisory
    locks alembic relies on — migrations must own a stable backend.
    """
    url = (
        os.environ.get("ALEMBIC_DATABASE_URL")
        or os.environ.get("HERMES_CRM_DATABASE_URL")
        or os.environ.get("CRM_DATABASE_URL")
        or config.get_main_option("sqlalchemy.url")
    )
    if not url:
        raise RuntimeError(
            "No database URL configured. Set HERMES_CRM_DATABASE_URL in "
            f"{_ENV_PATH} or pass sqlalchemy.url via alembic.ini."
        )
    return url


config.set_main_option("sqlalchemy.url", _resolve_database_url())

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

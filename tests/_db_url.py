"""Shared CRM database URL resolver for tests.

The leading underscore keeps pytest from collecting this module as tests.

Mirrors alembic/env.py's resolution: prefer ``HERMES_CRM_DATABASE_URL`` /
``CRM_DATABASE_URL`` env vars, then fall back to parsing the repo-root
``.env`` file. Returns ``None`` when nothing is configured so callers can
``pytest.skip`` cleanly.
"""
from __future__ import annotations

import os
from pathlib import Path


def resolve_database_url() -> str | None:
    url = os.environ.get("HERMES_CRM_DATABASE_URL") or os.environ.get("CRM_DATABASE_URL")
    if url:
        return url
    # Fallback: parse repo-root .env the same way alembic/env.py does.
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k in ("HERMES_CRM_DATABASE_URL", "CRM_DATABASE_URL"):
                return v
    return None

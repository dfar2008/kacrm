from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from fastapi.responses import RedirectResponse

# Dev convenience: auto-load a ``.env`` at the repo root so contributors
# don't need to source it manually before running uvicorn. Production
# (docker-compose) passes env directly — this import is a no-op there.
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[assignment]

_repo_root = Path(__file__).resolve().parents[2]

if load_dotenv is not None:
    _env_path = _repo_root / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=False)

# Make the vendored ``hermes/`` submodule importable. It's checked in as
# a git submodule pointing at upstream and isn't pip-installed into the
# venv, but hermes_cloud.app.main imports ``hermes_state`` (for session
# DB reads) — without this prepend, /app chat routes 500 with
# ModuleNotFoundError. Idempotent; no-op once uvicorn reload re-runs us.
_hermes_dir = _repo_root / "hermes"
if _hermes_dir.is_dir() and str(_hermes_dir) not in sys.path:
    sys.path.insert(0, str(_hermes_dir))

# Apply hermes-patches/*.patch BEFORE importing anything from hermes_cloud
# that transitively imports hermes.crm.db (which is created by patch 24
# and modified by patch 34). Otherwise Python imports the unpatched module
# first and our changes are dead. main.py also calls the patcher inside
# create_app() but by then it's too late — the module is already cached.
if os.environ.get("HERMES_CLOUD_SKIP_PATCHES") != "1":
    try:
        from hermes_cloud.app.hermes_patcher import ensure_hermes_patches_applied
        _patches_dir = _repo_root / "hermes-patches"
        if _hermes_dir.is_dir() and _patches_dir.is_dir():
            ensure_hermes_patches_applied(_hermes_dir, _patches_dir)
    except Exception as _exc:
        # Don't crash dev server on patch errors — main.py will retry and
        # log loudly. This early apply is a best-effort optimization.
        logging.getLogger(__name__).warning(
            "early hermes-patches apply failed: %r", _exc,
        )

from hermes_cloud.app.config import (
    AppConfig,
    cloud_orchestrate_specialists_enabled,
)
from hermes_cloud.app.main import create_app

logger = logging.getLogger(__name__)

DEFAULT_DEV_ADMIN_EMAIL = "admin@hermes.local"
DEFAULT_DEV_ADMIN_PASSWORD = "admin123"

def _detect_hermes_bin() -> str:
    """Auto-detect the hermes binary in the same venv as the running Python."""
    import sys
    venv_bin = Path(sys.executable).parent / "hermes"
    if venv_bin.exists():
        return str(venv_bin)
    return "hermes"


def create_dev_app():
    admin_email = os.getenv("HERMES_CLOUD_ADMIN_EMAIL", DEFAULT_DEV_ADMIN_EMAIL)
    admin_password = os.getenv("HERMES_CLOUD_ADMIN_PASSWORD", DEFAULT_DEV_ADMIN_PASSWORD)
    db_url = os.getenv("HERMES_CRM_DATABASE_URL") or os.getenv("HERMES_CLOUD_DB_URL")
    if not db_url:
        raise RuntimeError(
            "HERMES_CRM_DATABASE_URL must be set (PostgreSQL). "
            "Example: postgresql+psycopg://user@localhost:5432/hermes_crm_dev"
        )
    internal_key = os.getenv("HERMES_CLOUD_INTERNAL_KEY", "dev-internal-key")
    port_start = int(os.getenv("HERMES_CLOUD_PORT_RANGE_START", "9100"))
    port_end = int(os.getenv("HERMES_CLOUD_PORT_RANGE_END", "9999"))
    hermes_bin = os.getenv("HERMES_BIN") or _detect_hermes_bin()
    hermes_home_root = os.getenv("HERMES_HOME_ROOT") or None

    app = create_app(
        AppConfig(
            session_store_url=db_url,
            # Self-registration disabled — only admin can add users via
            # /api/admin/users (admin SPA → Users page). Login page hides
            # the "register" link based on /api/config/registration-status.
            allow_registration=False,
            internal_key=internal_key,
            port_range_start=port_start,
            port_range_end=port_end,
            # idle_timeout_seconds=0: processes run permanently; only crashed ones are reaped.
            hermes_bin=hermes_bin,
            hermes_home_root=hermes_home_root,
            cloud_orchestrate_specialists=cloud_orchestrate_specialists_enabled(),
        )
    )

    def _ensure_dev_admin() -> None:
        store = app.state.auth_store
        try:
            store.create_user_by_admin(admin_email, admin_password, is_admin=True)
            logger.info("Dev admin: %s / %s", admin_email, admin_password)
        except ValueError:
            if hasattr(store, "list_users") and hasattr(store, "update_user"):
                existing = next(
                    (u for u in store.list_users() if getattr(u, "email", "").lower() == admin_email.lower()),
                    None,
                )
                if existing is not None and not getattr(existing, "is_admin", False):
                    store.update_user(existing.id, is_admin=True)

    _ensure_dev_admin()
    logger.info(
        "Profile mode: each user gets their own hermes process on ports %d–%d",
        port_start, port_end,
    )

    @app.get("/", include_in_schema=False)
    def root_redirect():
        return RedirectResponse(url="/app/")

    return app


app = create_dev_app()

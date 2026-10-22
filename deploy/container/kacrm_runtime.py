"""Container roles. Credentials are supplied at runtime, never during build."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import sys

REQUIRED = (
    "HERMES_CRM_DATABASE_URL", "HERMES_CLOUD_INTERNAL_KEY", "HERMES_SESSION_SECRET",
    "HERMES_CLOUD_ADMIN_EMAIL", "HERMES_CLOUD_ADMIN_PASSWORD",
)


def configure() -> None:
    for name in (*REQUIRED, "ALEMBIC_DATABASE_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"):
        secret_file = os.environ.get(name + "_FILE")
        if secret_file:
            if os.environ.get(name):
                raise RuntimeError(f"Set either {name} or {name}_FILE, not both")
            os.environ[name] = Path(secret_file).read_text().rstrip("\r\n")
            del os.environ[name + "_FILE"]  # Child Web factory revalidates resolved values.
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        raise RuntimeError("Missing runtime settings: " + ", ".join(missing))
    os.environ.setdefault("HERMES_CLOUD_DB_URL", os.environ["HERMES_CRM_DATABASE_URL"])
    os.environ.setdefault("HERMES_CRM_ADMIN_EMAIL", os.environ["HERMES_CLOUD_ADMIN_EMAIL"])
    os.environ.setdefault("HERMES_CRM_ADMIN_PASSWORD", os.environ["HERMES_CLOUD_ADMIN_PASSWORD"])
    if os.environ["HERMES_CRM_ADMIN_EMAIL"] != os.environ["HERMES_CLOUD_ADMIN_EMAIL"]:
        raise RuntimeError("CRM and application admin emails must match")
    if os.environ["HERMES_CRM_ADMIN_PASSWORD"] != os.environ["HERMES_CLOUD_ADMIN_PASSWORD"]:
        raise RuntimeError("CRM and application admin passwords must match")


def create_app():
    configure()
    from hermes_cloud.app.config import AppConfig, cloud_orchestrate_specialists_enabled
    from hermes_cloud.app.main import create_app as factory
    app = factory(AppConfig(
        service_name="kacrm",
        session_store_url=os.environ["HERMES_CRM_DATABASE_URL"],
        session_cookie_secret=os.environ["HERMES_SESSION_SECRET"],
        allow_registration=False,
        internal_key=os.environ["HERMES_CLOUD_INTERNAL_KEY"],
        port_range_start=int(os.getenv("HERMES_CLOUD_PORT_RANGE_START", "9310")),
        port_range_end=int(os.getenv("HERMES_CLOUD_PORT_RANGE_END", "9399")),
        hermes_bin=os.environ["HERMES_BIN"],
        hermes_home_root=os.environ["HERMES_HOME_ROOT"],
        cloud_orchestrate_specialists=cloud_orchestrate_specialists_enabled(),
    ))
    store = app.state.auth_store
    try:
        store.create_user_by_admin(
            os.environ["HERMES_CLOUD_ADMIN_EMAIL"],
            os.environ["HERMES_CLOUD_ADMIN_PASSWORD"], is_admin=True,
        )
    except ValueError:
        # Existing account credentials are preserved, as in source deployment.
        existing = next((u for u in store.list_users()
                         if u.email.lower() == os.environ["HERMES_CLOUD_ADMIN_EMAIL"].lower()), None)
        if existing is None or not existing.is_admin:
            raise RuntimeError("Configured administrator is missing or is not an administrator") from None
    from fastapi.responses import RedirectResponse
    app.add_api_route("/", lambda: RedirectResponse("/app/"), include_in_schema=False)
    return app


def main(args: list[str]) -> None:
    role = args[0] if args else "web"
    if role not in {"web", "worker", "beat", "init"}:
        # Explicit diagnostic/custom command, e.g. `docker run IMAGE python --version`.
        os.execvp(role, args)
    configure()
    if role == "init":
        from alembic import command
        from alembic.config import Config
        command.upgrade(Config("/opt/kacrm/alembic.ini"), "head")
        from hermes.crm.seed import seed
        from hermes.crm.db import db_session
        with db_session() as db:
            existing_users = db.fetchcolumn("SELECT COUNT(*) FROM users")
        if existing_users:
            print("KaCRM database migrated; existing users and role grants preserved", flush=True)
            return
        # Seed prints a generated admin API key: never send it to container logs.
        with contextlib.redirect_stdout(io.StringIO()):
            seed()
        print("KaCRM database initialized", flush=True)
        return
    if role == "web":
        cmd = ["uvicorn", "deploy.container.kacrm_runtime:create_app", "--factory",
               "--host", "0.0.0.0", "--port", os.getenv("PORT", "8765")]
    else:
        if not os.environ.get("CELERY_BROKER_URL"):
            raise RuntimeError("Missing runtime setting: CELERY_BROKER_URL")
        cmd = ["celery", "-A", "hermes_cloud.app.celery_app", role, "--loglevel=info"]
        if role == "worker":
            cmd += ["--concurrency", os.getenv("KACRM_WORKER_CONCURRENCY", "2")]
        else:
            cmd += ["--schedule", "/var/lib/kacrm/celerybeat-schedule", "--pidfile="]
    os.execvp(cmd[0], cmd + args[1:])


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except RuntimeError as exc:
        print(f"KaCRM: {exc}", file=sys.stderr)
        sys.exit(2)

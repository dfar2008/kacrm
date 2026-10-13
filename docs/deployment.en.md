# KaCRM Deployment Guide

**English** · [简体中文](deployment.md)

Source updated: 2026-10-12. English edition: 2026-10-13. Adapted from the hermes-cloud deployment instructions for the current KaCRM codebase.

Hermes Agent is pinned to **v0.21.6**, commit `818c13be1dc4fd28987e1e881a9408224afd4535`. The database migration head is **0115**. Deploy the pinned version with its seven patches rather than tracking upstream main. See the [v0.21.6 Upgrade Notes](2026-10-12-hermes-v0.21.6-upgrade.en.md) for historical changes and verification. Those test results belong to the source project's upgrade run and do not establish that every new deployment passes.

This version supports source deployment only. Docker deployment is not supported.

## 1. Architecture and Requirements

- React SPA: builds into `frontend/dist/`, served by FastAPI at `/app/`.
- FastAPI: CRM APIs, multi-tenant profile management, and authentication.
- Hermes submodule with local patches: one multiplex gateway serves isolated profiles, messaging channels, and agent cron by default.
- PostgreSQL and pgvector: identities, CRM records, sessions, and messages share the business database.
- PgBouncer: transaction pooling; Alembic connects directly to PostgreSQL.
- Redis, Celery workers, and beat: asynchronous jobs, workflow scheduling, and retention tasks.

Install Git, uv, Python 3.11, Node.js 20.19+ or 22.12+, npm, PostgreSQL, pgvector, and Redis. The full supervisor also requires PgBouncer and Bash. Production examples use Linux/systemd and an HTTPS reverse proxy. Configure model and channel credentials through the administrative interface.

## 2. Clone and Install Dependencies

```bash
git clone --recurse-submodules https://github.com/dfar2008/kacrm.git
cd kacrm
git submodule update --init --recursive
git -C hermes describe --tags --always
git -C hermes rev-parse HEAD

uv venv .venv --python 3.11
uv pip install --python .venv/bin/python -e '.[dev]'
uv pip install --python .venv/bin/python --no-deps -e ./hermes
```

The version checks should return `v0.21.6` and the pinned SHA above. The parent project and Hermes share this external virtual environment. Do not run `uv sync` in it: synchronization can remove additionally installed dependencies.

Many upstream dependency declarations are conditional on Python 3.14; editable installation on Python 3.11 does not install all of them. Install the basic agent runtime dependencies explicitly:

```bash
uv pip install --python .venv/bin/python \
  openai==2.24.0 fire==0.7.1 rich==14.3.3 tenacity==9.1.4 \
  'httpx[socks]==0.28.1' ruamel.yaml==0.18.16 jinja2==3.1.6 \
  prompt_toolkit==3.0.52 psutil==7.2.2 Markdown==3.10.2 \
  'PyJWT[crypto]==2.13.0' 'aiohttp>=3.13.3,<4' \
  truststore==0.10.4 snowballstemmer==3.1.1 tomli-w==1.2.0 nemo-relay==0.9.3

cd frontend
npm ci
npm run build
cd ..
```

This is the basic environment. Enabled models, channels, browser tools, and document tools may require additional dependencies. Install the relevant extras from `hermes/pyproject.toml` and verify the features you use.

## 3. Database and Authentication Configuration

Copy `.env.example` to `.env` and replace the example values. Change `postgresql+psycopg2` to `postgresql+psycopg`. The following assumes a database named `kacrm` and a database user named `kacrm` with migration permissions already exist.

```dotenv
# Same database for both application URLs; migrations bypass PgBouncer.
HERMES_CLOUD_DB_URL=postgresql+psycopg://kacrm:REPLACE_DB_PASSWORD@127.0.0.1:6432/kacrm
HERMES_CRM_DATABASE_URL=postgresql+psycopg://kacrm:REPLACE_DB_PASSWORD@127.0.0.1:6432/kacrm
ALEMBIC_DATABASE_URL=postgresql+psycopg://kacrm:REPLACE_DB_PASSWORD@127.0.0.1:5432/kacrm
HERMES_CLOUD_REQUIRE_DB=1
CELERY_BROKER_URL=redis://127.0.0.1:6379/3
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/3

# Seed and dev_server must use the same administrator email and password.
HERMES_CRM_ADMIN_USERNAME=admin
HERMES_CRM_ADMIN_EMAIL=admin@example.com
HERMES_CRM_ADMIN_PASSWORD=REPLACE_ADMIN_PASSWORD
HERMES_CLOUD_ADMIN_EMAIL=admin@example.com
HERMES_CLOUD_ADMIN_PASSWORD=REPLACE_ADMIN_PASSWORD
HERMES_CLOUD_INTERNAL_KEY=REPLACE_INTERNAL_KEY
HERMES_SESSION_SECRET=REPLACE_SESSION_SECRET

# Replace with actual installation paths and separate writable directories.
HERMES_BIN=/opt/kacrm/.venv/bin/hermes
HERMES_PYTHON=/opt/kacrm/.venv/bin/python
HERMES_HOME_ROOT=/var/lib/kacrm
CLOUD_INTERNAL_URL=http://127.0.0.1:8765
PORT=8765
HERMES_CLOUD_PORT_RANGE_START=9310
HERMES_CLOUD_PORT_RANGE_END=9399
HERMES_CLOUD_SCALE_TO_ZERO=1
HERMES_CLOUD_IDLE_TIMEOUT_MINUTES=5
```

Generate the internal and session secrets independently, for example with `.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))'`. The session-signing variable is `HERMES_SESSION_SECRET`. Restrict access to `.env` and keep it out of Git. Use a separate Redis database or instance so another project's Celery workers do not consume these tasks.

The default gateway port range starts at `9100`; multiplex uses the first configured port. Multiple installations on one machine need non-overlapping port ranges and separate `HERMES_HOME_ROOT` directories. Verify that the example range `9310–9399` is free. Port conflicts can make Hermes exit and the web status turn red.

Configure the `kacrm` alias under `[databases]` in `pgbouncer/pgbouncer.ini` to point to the PostgreSQL database. The repository's `auth_type=trust` and `openclaw` user are local examples. Replace them with production authentication settings; SCRAM is recommended. Ensure the service user can access authentication files, logs, PID files, and profile directories.

For local web/API-only verification, both application URLs can connect directly to port `5432`. The full supervisor starts PgBouncer and checks port `6432`, so complete its configuration before using it.

## 4. Apply Patches, Migrate, and Seed

Apply patches to a fresh, clean submodule first so `hermes.crm` can be imported:

```bash
export PYTHONPATH="$PWD:$PWD/hermes"
.venv/bin/python - <<'PY'
from pathlib import Path
from hermes_cloud.app.hermes_patcher import ensure_hermes_patches_applied
root = Path.cwd()
ensure_hermes_patches_applied(root / 'hermes', root / 'hermes-patches')
PY

set -a
. ./.env
set +a
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m alembic current
.venv/bin/python -m alembic heads
.venv/bin/python -c 'from hermes.crm.seed import seed; seed()'
```

`current` and `heads` should agree; the current head is `0115`. Run seed after migration to initialize the administrator, CRM modules, and metadata. Re-running seed is not a password-reset procedure for an existing administrator. Seed may print a new administrator API key; protect its logs as credentials.

The patcher may reset and clean a dirty or incomplete submodule. Save personal changes before running it. A dirty submodule after patching is expected overlay state. Development startup permits patch replay by default. For a verified, pre-patched release directory, set `HERMES_CLOUD_SKIP_PATCHES=1` so runtime startup uses the prepared artifact without resetting source files.

## 5. Start and Verify

For web/API-only verification:

```bash
set -a
. ./.env
set +a
export PYTHONPATH="$PWD:$PWD/hermes"
.venv/bin/python -m uvicorn hermes_cloud.app.dev_server:app \
  --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/app/` and sign in with the configured administrator account. This command does not start Celery, so it does not verify asynchronous jobs or scheduled workflows.

For the full environment:

```bash
bash scripts/run_cloud_supervised.sh
```

The supervisor reads the root `.env`, manages PgBouncer, Uvicorn, Celery workers, and beat, and writes to `logs/`. It cleans up older processes of the same kind; other installations on the machine need separate deployment arrangements. The script listens on `0.0.0.0`; control external access through a firewall and HTTPS reverse proxy.

For a persistent Linux service, adapt `deploy/kacrm-cloud.service`: set the service user, directories, and ExecStart, then install it and run `systemctl daemon-reload` and `systemctl enable --now kacrm-cloud`. The unit's stop timeout is 60 seconds; the supervisor currently waits 15 seconds before forcefully terminating children. Check both limits. They do not establish full request draining or zero downtime.

When renaming an existing deployment, stop and disable the old service before installing `kacrm-cloud.service`. Move existing profiles to `~/.kacrm/profiles`, or explicitly retain their path using `HERMES_HOME_ROOT` and the deployment script's `PROFILES_ROOT`. Check configuration, credentials, and file permissions before starting the new service. Renaming this repository does not migrate server data automatically.

Verify at least:

1. `curl --fail http://127.0.0.1:8765/health` returns 200.
2. After authentication, `/crm/modules` and `/crm/records/leads?limit=1` return 200 and the interface loads.
3. Configure your model, exchange real messages, and invoke a tool. Confirm history persists after a refresh.
4. Verify enabled messaging channels, approval callbacks, and tenant isolation.
5. Verify Celery workers and beat, workflow scheduling, profile cron, and restart recovery.

## 6. Update an Existing Server

Complete upgrade verification in a test environment first. `scripts/deploy-to-server.sh` targets an already configured server. By default, it generates a plan and builds local artifacts; remote writes require `--apply`.

```bash
export SSH_HOST=ubuntu@YOUR_SERVER
export SSH_KEY="$HOME/.ssh/YOUR_DEPLOY_KEY"
export REMOTE=/opt/kacrm
export MODE=source
bash scripts/deploy-to-server.sh
# After reviewing the plan, backups, and test results:
bash scripts/deploy-to-server.sh --apply
```

The script enforces the v0.21.6 SHA, sends a pre-patched Hermes checkout and installation marker, rebuilds the frontend, then backs up, stops services, synchronizes files, installs additional dependencies, migrates, seeds, restarts, and verifies health and authenticated CRM APIs.

`203.0.113.10` is a placeholder. The script still assumes service name `kacrm-cloud`, database `hermes_crm`, remote user `ubuntu`, and particular profile paths. Adapt the script for your database and account; changing SSH_HOST alone is insufficient. It adds only four upgrade dependencies and is not a complete fresh-machine installer. Use `SKIP_MIGRATE=1` only when you have verified there are no new migrations.

## 7. Backups, Rollback, and Upgrade Gates

Before migration, save a full PostgreSQL dump through a direct connection, deployed code and patches with their SHA, frontend build, installation marker, `.env`, and profile directories. These backups include account, model, and channel credentials; store them in a controlled location.

Treat code rollback and database recovery separately. Automatic script rollback does not restore the database. If the new schema is incompatible with old code, stop the service, verify restoring the backup in a separate database, and switch only after validation. Some migrations drop tables or modify data; `alembic downgrade` is not a general recovery procedure. Re-verify actual business behavior after recovery.

Before a Hermes upgrade, validate the candidate stable tag and replay all seven patches in order in an isolated checkout. Run `scripts/upgrade-acceptance.sh`, cloud regression tests, independent real-PostgreSQL tests, and affected upstream tests. Follow `hermes/tests/AGENTS.md` and use `scripts/run_tests.sh` for per-file upstream test isolation. When changing the pin, update patches, migrations, deployment version checks, and installation markers together. Do not use `git submodule update --remote` as the upgrade procedure.

The historical upgrade notes identify real Feishu approval clicks and cross-client synchronization as unverified; include them in release acceptance. This guide supersedes older SQLite, standalone worker-cluster, WeChat Pay, RL, and auto_drive deployment procedures.

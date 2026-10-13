#!/usr/bin/env bash
# Deploy hermes-cloud to the production server.
#
# Modes (env MODE):
#   source (default) — hermes_cloud shipped as plaintext .py
#   pyc              — hermes_cloud shipped as sourceless .pyc (encrypted-ish,
#                      built by build-pyc-artifact.sh)
#
# hermes/ ALWAYS ships pre-patched plaintext .py; the server runs the
# patcher OFF (HERMES_CLOUD_SKIP_PATCHES=1) so it never resets our tree.
#
# Pipeline (on --apply):
#   backup (code + pg_dump) -> stop -> rsync -> migrate -> SEED -> restart ->
#   VERIFY (/health 200 AND authenticated /crm/records/leads 200) ->
#   auto-rollback on any verify failure.
#
# Lessons baked in (2026-05-29 incident): prod's systemd start path never
# ran the seed, so business CRM modules were missing -> 404. And the old
# deploy only checked /health, missing the CRM breakage. This script now
# always seeds and always verifies real CRM before declaring success.
#
# DEFAULT IS DRY-RUN. Pass --apply to touch the server.
set -euo pipefail

SSH_KEY="${SSH_KEY:-$HOME/.ssh/kacrm_deploy}"
SSH_HOST="${SSH_HOST:-ubuntu@203.0.113.10}"
REMOTE="${REMOTE:-/opt/kacrm}"
MODE="${MODE:-source}"
SSH="ssh -i $SSH_KEY -o ConnectTimeout=25"
RSH="ssh -i $SSH_KEY -o ConnectTimeout=25"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
OUT="$repo_root/dist/deploy"
APPLY=0; [[ "${1:-}" == "--apply" ]] && APPLY=1
say(){ echo "[deploy] $*"; }

# ── 1. Verify + stage hermes/ (pre-patched v0.21.6, from live tree) ───
src_hermes="$repo_root/hermes"
[[ "$(git -C "$src_hermes" rev-parse HEAD)" == 818c13be1* ]] \
    || { echo "FATAL: hermes/ not at v0.21.6 (818c13be)" >&2; exit 1; }
grep -q "surface ACL hard-check" "$src_hermes/agent/tool_executor.py" \
    || { echo "FATAL: hermes/ not patched (patch 30 hook missing)" >&2; exit 1; }
[[ "$(find "$src_hermes" -name '*.rej' | wc -l | tr -d ' ')" == "0" ]] \
    || { echo "FATAL: stray .rej in hermes/" >&2; exit 1; }

# v0.21.6 resolves packaged version/update ownership from an install stamp.
# Production has no .git and is updated by this deployment pipeline.
mkdir -p "$OUT"
"$repo_root/.venv/bin/python" "$src_hermes/scripts/write_install_stamp.py" \
    --output "$OUT/install-stamp.json" \
    --commit "$(git -C "$src_hermes" rev-parse HEAD)" \
    --base-version 0.21.6 --distance 0 --dirty \
    --source ci --update-mechanism external

# ── 2. Stage hermes_cloud (source .py or .pyc) ────────────────────────
if [[ "$MODE" == "pyc" ]]; then
    say "MODE=pyc — building sourceless .pyc artifact"
    "$repo_root/scripts/build-pyc-artifact.sh" >/dev/null
    hc_src="$OUT/hermes_cloud/"
else
    say "MODE=source — shipping hermes_cloud as plaintext .py"
    hc_src="$repo_root/hermes_cloud/"
fi

# ── 2b. Build frontend (Vite SPA) ─────────────────────────────────────
# The SPA is served as static files by the backend; without rebuilding,
# frontend code changes (e.g. menu visibility) never reach prod even if
# the .py is synced. Build now so dist/ is ready to ship.
say "build frontend (npm run build)"
( cd "$repo_root/frontend" && npm run build 2>&1 | tail -3 ) \
    || { echo "FATAL: frontend build failed" >&2; exit 1; }
[[ -f "$repo_root/frontend/dist/index.html" ]] \
    || { echo "FATAL: frontend/dist/index.html missing after build" >&2; exit 1; }

cat <<EOF

[deploy] ───────────── PLAN (MODE=$MODE) ─────────────
  target : $SSH_HOST:$REMOTE
  hermes/      <- pre-patched v0.21.6 plaintext (--delete, excl .git)
  install stamp<- v0.21.6 provenance; deployment owns updates
  runtime deps : targeted truststore / snowballstemmer / tomli-w / nemo-relay install
  hermes_cloud/<- $([[ "$MODE" == pyc ]] && echo ".pyc artifact" || echo "source .py") (--delete)
  frontend/dist/<- built SPA (--delete; rebuilds index hash, hard-reload invalidates)
  alembic/, scripts/ <- synced (no --delete)
  .env         <- HERMES_CLOUD_SKIP_PATCHES=1
  migrate      : alembic upgrade head (BEFORE seed; SKIP_MIGRATE=1 to skip)
  seed         : hermes.crm.seed (idempotent: modules/roles/grants)
  systemd unit : deploy/kacrm-cloud.service -> /etc/systemd/system (diff-gated + daemon-reload)
  stop         : sudo systemctl stop kacrm-cloud before syncing / migration
  restart      : sudo systemctl restart kacrm-cloud
  verify       : /health==200 AND Bearer /crm/records/leads==200  (else AUTO-ROLLBACK)
  backup       : hermes/ + hermes_cloud/ -> .bak-<ts>, pg_dump hermes_crm
EOF

if [[ "$APPLY" != "1" ]]; then
    echo; say "DRY-RUN. Re-run with --apply."
    exit 0
fi

ts="$(date +%Y%m%d-%H%M%S)"

# ── 3. Backup (code + DB) ─────────────────────────────────────────────
# 备份保留份数(留最新 N 份,旧的自动清,免撑满磁盘)。可用 KEEP_BACKUPS 覆盖。
KEEP_BACKUPS="${KEEP_BACKUPS:-5}"
say "backup -> .bak-$ts + pg_dump (rotate: keep latest $KEEP_BACKUPS)"
$SSH "$SSH_HOST" "cd '$REMOTE' && cp -a hermes 'hermes.bak-$ts' && cp -a hermes_cloud 'hermes_cloud.bak-$ts' && sudo -u postgres pg_dump hermes_crm > ~/hermes_crm_predeploy-$ts.sql && echo '  backed up code + db'"
# 轮转:每类备份只留最新 N 份,删更旧的(hermes.bak / hermes_cloud.bak 在 \$REMOTE;pg_dump 在 ~)。
$SSH "$SSH_HOST" "cd '$REMOTE' && \
  ls -d hermes.bak-* 2>/dev/null | sort -r | tail -n +\$(( $KEEP_BACKUPS + 1 )) | xargs -r rm -rf && \
  ls -d hermes_cloud.bak-* 2>/dev/null | sort -r | tail -n +\$(( $KEEP_BACKUPS + 1 )) | xargs -r rm -rf && \
  ls ~/hermes_crm_predeploy-*.sql 2>/dev/null | sort -r | tail -n +\$(( $KEEP_BACKUPS + 1 )) | xargs -r rm -f && \
  echo \"  rotated backups (kept latest $KEEP_BACKUPS); disk: \$(df -h / | awk 'NR==2{print \$4\" free\"}')\""

rollback() {
    trap - ERR  # never recursively retry a failed rollback
    say "!!! AUTO-ROLLBACK (restore .bak-$ts) !!!"
    $SSH "$SSH_HOST" "cd '$REMOTE' && rm -rf hermes hermes_cloud && mv 'hermes.bak-$ts' hermes && mv 'hermes_cloud.bak-$ts' hermes_cloud && sudo systemctl restart kacrm-cloud && echo '  rolled back to pre-deploy code'"
}

# A running gateway can hold agent_sessions then agent_messages while the
# migration needs both exclusively. Stop the service (including its gateway
# children) before replacing source files or taking schema locks.
say "stop kacrm-cloud for code sync and migration"
$SSH "$SSH_HOST" "sudo systemctl stop kacrm-cloud"
# Restore service/code on any subsequent unhandled failure, including rsync.
trap 'deploy_status=$?; trap - ERR; rollback; exit "$deploy_status"' ERR

# ── 4. rsync code ─────────────────────────────────────────────────────
say "rsync hermes/ (pre-patched)"
rsync -az --delete --exclude='.git' --exclude='/.hermes/' --exclude='/.venv/' --exclude='/node_modules/' --exclude='/test_durations.json' --exclude='__pycache__' --exclude='*.pyc' -e "$RSH" "$src_hermes/" "$SSH_HOST:$REMOTE/hermes/"
rsync -az -e "$RSH" "$OUT/install-stamp.json" "$SSH_HOST:$REMOTE/hermes/install-stamp.json"
say "rsync hermes_cloud/ ($MODE)"
rsync -az --delete --exclude='__pycache__' -e "$RSH" "$hc_src" "$SSH_HOST:$REMOTE/hermes_cloud/"
say "rsync frontend/dist/ (built SPA)"
$SSH "$SSH_HOST" "mkdir -p '$REMOTE/frontend/dist'"
rsync -az --delete -e "$RSH" "$repo_root/frontend/dist/" "$SSH_HOST:$REMOTE/frontend/dist/"
say "rsync alembic/ + scripts/"
rsync -az --exclude='__pycache__' -e "$RSH" "$repo_root/alembic/" "$SSH_HOST:$REMOTE/alembic/"
rsync -az --exclude='__pycache__' -e "$RSH" "$repo_root/scripts/" "$SSH_HOST:$REMOTE/scripts/"

# Cloud keeps its own mixed venv: never run uv sync here (it removes
# editable-installed Hermes dependencies). Install only new runtime imports.
say "ensure v0.21.6 runtime dependencies"
$SSH "$SSH_HOST" "cd '$REMOTE' && \
    if command -v uv >/dev/null 2>&1; then \
        uv pip install --python .venv/bin/python truststore==0.10.4 snowballstemmer==3.1.1 tomli-w==1.2.0 nemo-relay==0.9.3; \
    elif [ -x /home/ubuntu/.local/bin/uv ]; then \
        /home/ubuntu/.local/bin/uv pip install --python .venv/bin/python truststore==0.10.4 snowballstemmer==3.1.1 tomli-w==1.2.0 nemo-relay==0.9.3; \
    else \
        .venv/bin/python -m pip install truststore==0.10.4 snowballstemmer==3.1.1 tomli-w==1.2.0 nemo-relay==0.9.3; \
    fi" || { say "runtime dependency install failed"; rollback; exit 1; }

# ── 4c. Sync bundled skills → per-profile copies (defeat curator shadow) ──
# Per-profile skill copies (~/.kacrm/profiles/*/skills/) shadow the bundled
# hermes/skills/ ones and never auto-update, so edits to a bundled skill never
# reach a running profile's agent (2026-06-21 incident: schema-evolution ai_fill
# guidance shipped but the agent kept reading its stale profile copy). Refresh
# only system skills present in both; profile-only skills are left untouched.
# The subsequent `systemctl restart kacrm-cloud` reloads agent tool code so new
# tool params (e.g. ai_fill) take effect too. Non-fatal.
say "sync bundled skills → per-profile copies"
$SSH "$SSH_HOST" "cd '$REMOTE' && bash scripts/sync-profile-skills.sh '${PROFILES_ROOT:-/home/ubuntu/.kacrm/profiles}' '$REMOTE/hermes/skills'" \
    || say "  skill sync non-fatal failure (continuing)"

# ── 4d. Ensure mmx-cli (MiniMax CLI) — web_search backend ─────────────
# The `minimax` web_search provider shells out to `mmx search query`. Install
# the CLI globally if missing (idempotent). MiniMax-hosted search avoids the
# scraping/anti-bot fragility of Baidu. Non-fatal — Baidu remains the fallback.
say "ensure mmx-cli (MiniMax CLI web search)"
$SSH "$SSH_HOST" "if ! command -v mmx >/dev/null 2>&1; then sudo npm install -g mmx-cli >/dev/null 2>&1; fi; mmx --version 2>/dev/null || true" \
    || say "  mmx-cli ensure non-fatal failure (Baidu fallback)"

# ── 4e. Sync systemd unit (source of truth: deploy/kacrm-cloud.service) ──
# The unit was historically hand-provisioned on the box and lived nowhere in
# the repo, so its TimeoutStopSec could silently drift below the cloud
# supervisor's scale-to-zero drain budget (_drain_active_agents: 30s + 15s
# margin ≈ 45s) — and a restart mid-drain would SIGKILL in-flight sessions,
# exactly what the feature avoids. Ship it from the repo, install diff-gated:
# only overwrite + daemon-reload when it actually differs, so the step-7
# restart picks up any change. Backs up the old unit. Non-fatal.
if [[ -f "$repo_root/deploy/kacrm-cloud.service" ]]; then
    say "sync systemd unit (deploy/kacrm-cloud.service, diff-gated)"
    rsync -az -e "$RSH" "$repo_root/deploy/kacrm-cloud.service" "$SSH_HOST:/tmp/kacrm-cloud.service.staged"
    $SSH "$SSH_HOST" "
        if ! sudo cmp -s /tmp/kacrm-cloud.service.staged /etc/systemd/system/kacrm-cloud.service; then
            sudo cp -a /etc/systemd/system/kacrm-cloud.service /etc/systemd/system/kacrm-cloud.service.bak-$ts 2>/dev/null || true
            sudo cp /tmp/kacrm-cloud.service.staged /etc/systemd/system/kacrm-cloud.service
            sudo systemctl daemon-reload
            echo \"  unit changed -> installed + daemon-reload (TimeoutStopUSec now: \$(systemctl show kacrm-cloud -p TimeoutStopUSec --value))\"
        else
            echo '  unit unchanged'
        fi
        rm -f /tmp/kacrm-cloud.service.staged" \
        || say "  systemd unit sync non-fatal failure (continuing)"
else
    say "  deploy/kacrm-cloud.service missing — skipping unit sync"
fi

# ── 5. env: patcher off ───────────────────────────────────────────────
$SSH "$SSH_HOST" "cd '$REMOTE' && (grep -q '^HERMES_CLOUD_SKIP_PATCHES=' .env && sed -i 's/^HERMES_CLOUD_SKIP_PATCHES=.*/HERMES_CLOUD_SKIP_PATCHES=1/' .env || echo 'HERMES_CLOUD_SKIP_PATCHES=1' >> .env)"

# ── 5b. MIGRATE DB to head (BEFORE seed/restart) ──────────────────────
# 历史坑:过去 deploy 只 rsync 迁移文件、不执行,prod 库滞后 → 新代码撞旧
# schema 报 500(自定义模块/附件落库/归档)。现在部署即升库。
# 顺序:在 seed 之前(seed 往迁移建的表里写),在 restart 之前(新代码起来
# 时 schema 已就位)。DB 已在 step 3 pg_dump 备份。
# alembic 每个迁移走独立事务(transactional DDL),失败的那个自动回滚;
# 失败时回滚代码并重启旧服务；迁移失败的事务自动回滚。
# SKIP_MIGRATE=1 可跳过(纯代码 hotfix,确知无新迁移时)。
if [[ "${SKIP_MIGRATE:-0}" != "1" ]]; then
    say "alembic upgrade head (DB migrate before seed)"
    $SSH "$SSH_HOST" "cd '$REMOTE' && set -o pipefail && set -a && . ./.env >/dev/null 2>&1 && set +a && \
        echo \"  current: \$(.venv/bin/python -m alembic current 2>/dev/null | tail -1)\" && \
        echo \"  heads:   \$(.venv/bin/python -m alembic heads 2>/dev/null | tail -1)\" && \
        .venv/bin/python -m alembic upgrade head 2>&1 | tail -10" \
        || { say "❌ alembic upgrade failed — DB 维持原状(pg_dump 在 ~/hermes_crm_predeploy-$ts.sql);回滚代码并中止"; rollback; exit 1; }
else
    say "SKIP_MIGRATE=1 — 跳过 alembic upgrade(纯代码部署)"
fi

# ── 6. SEED (idempotent: admin + collections/modules + field/view metadata) ─
# Seed from the canonical Hermes CRM implementation after migration.
say "seed (hermes.crm.seed)"
$SSH "$SSH_HOST" "cd '$REMOTE' && set -o pipefail && set -a && . ./.env >/dev/null 2>&1 && set +a && export PYTHONPATH=\$PWD:\$PWD/hermes && export HERMES_CRM_DATABASE_URL=\"\${ALEMBIC_DATABASE_URL:-\${HERMES_CRM_DATABASE_URL:-}}\" && .venv/bin/python -c 'from hermes.crm.seed import seed; seed()' 2>&1 | tail -6" || { say "seed failed"; rollback; exit 1; }

# ── 7. restart ────────────────────────────────────────────────────────
say "restart kacrm-cloud"
$SSH "$SSH_HOST" "sudo systemctl restart kacrm-cloud" || { rollback; exit 1; }

# Poll for the app to actually SERVE /health before verifying. The cloud
# takes ~30-40s to boot (hermes patch apply + per-profile subprocess
# adoption) — far longer than a fixed `sleep 14`, which made the CRM
# verify race a still-starting server and false-fail -> needless rollback
# (2026-06-04 incident). Wait up to 90s for a real 200 instead.
say "wait for :8765/health to serve 200 (up to 90s)"
$SSH "$SSH_HOST" 'for i in $(seq 1 30); do c=$(curl -s -o /dev/null -w %{http_code} -m 5 http://localhost:8765/health 2>/dev/null); [ "$c" = "200" ] && { echo "ready after $((i*3))s"; exit 0; }; sleep 3; done; echo "NOT READY after 90s (last=$c)"; exit 1' \
    || { say "server did not become ready in 90s"; rollback; exit 1; }

# ── 8. VERIFY real CRM (auto-rollback on fail) ────────────────────────
say "verify /health + authenticated /crm/records/leads"
# Heredoc fed to a remote `bash -s`, output captured via a temp file
# (heredoc inside $() confuses the parser). Quoted 'REMOTE' = no local
# expansion; the remote bash evaluates $key etc.
_vtmp="$(mktemp)"
$SSH "$SSH_HOST" "bash -s" > "$_vtmp" <<'REMOTE'
h=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8765/health)
key=$(sudo -u postgres psql -d hermes_crm -tAc "SELECT api_key FROM users WHERE is_admin=TRUE ORDER BY id LIMIT 1;" | tr -d '[:space:]')
c=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $key" 'http://localhost:8765/crm/records/leads?limit=1')
m=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $key" 'http://localhost:8765/crm/modules')
echo "health=$h crm_records=$c crm_modules=$m"
REMOTE
verify="$(cat "$_vtmp")"; rm -f "$_vtmp"
say "  $verify"
pycount="$($SSH "$SSH_HOST" "find $REMOTE/hermes_cloud -name '*.py' | wc -l | tr -d ' '" || true)"
if [[ "$verify" == *"health=200"* && "$verify" == *"crm_records=200"* && "$verify" == *"crm_modules=200"* ]]; then
    say "✅ DEPLOY OK (MODE=$MODE) — CRM verified: $verify"
    say "   server hermes_cloud .py count: $pycount  (source mode expects >0; pyc mode expects 0)"
else
    say "❌ VERIFY FAILED: $verify"
    rollback
    exit 1
fi

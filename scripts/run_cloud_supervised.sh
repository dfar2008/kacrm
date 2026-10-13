#!/bin/bash
# Cloud process supervisor — runs all three long-lived processes that
# the hermes-cloud server depends on, with per-process exponential
# backoff restart and unified SIGTERM/SIGINT shutdown.
#
# Children:
#   1. pgbouncer — transaction-pooling proxy in front of PostgreSQL
#   2. uvicorn  — the FastAPI app on $PORT
#   3. celery worker — executes async tasks
#   4. celery beat   — schedules periodic tasks (daily-coach, weekly-digest, etc.)
#
# Why all three here: prior to 2026-05-18 only uvicorn was supervised;
# celery worker/beat were "production only" and silently never ran
# locally, so every periodic task (most importantly the morning
# crm-daily-coach push) was a phantom — only manual SPA clicks made
# it look like cron worked. Foundational infra runs the same in dev
# as in prod.
#
# Behavior per child:
# * exits non-zero -> sleep `backoff` (starts 2s, doubles, caps 60s), restart.
# * ran >60s before dying -> reset backoff to 2s.
# * SIGTERM/SIGINT to the supervisor -> propagate to all children, exit.
#
# Logs:
#   logs/uvicorn.log
#   logs/celery-worker.log
#   logs/celery-beat.log
# (also tee'd to stdout for preview_logs visibility)

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
set -a
# shellcheck disable=SC1091
. ./.env
set +a

# The hermes/ submodule is a PEP-420 namespace package — importable
# only when its parent dir is on sys.path. uvicorn auto-adds cwd, but
# the `celery` entry-point script doesn't, so prefork workers raise
# ModuleNotFoundError on the first task. Setting PYTHONPATH unifies
# both binaries. Use the computed project root ($ROOT) — never a
# machine-specific absolute path, so this is correct on any host.
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

PORT="${PORT:-8765}"
LOG_DIR="logs"
mkdir -p "$LOG_DIR"

# pgbouncer fronts PostgreSQL on :6432 (see pgbouncer/pgbouncer.ini and
# .env). The app connects through it so per-process connection pools
# don't exhaust PG max_connections as profile subprocesses multiply.
PGBOUNCER_BIN="$(command -v pgbouncer || true)"
if [ -z "$PGBOUNCER_BIN" ]; then
    echo "[supervisor] FATAL: pgbouncer not found on PATH — refusing to start." >&2
    echo "[supervisor]   install it: brew install pgbouncer" >&2
    exit 2
fi

# Sanity check: every periodic task lives in celery_app now (no asyncio
# fallback). If broker isn't configured the worker/beat will crash
# loop; surface the misconfig loudly instead of silently degrading.
if [ -z "${CELERY_BROKER_URL:-}" ]; then
    echo "[supervisor] FATAL: CELERY_BROKER_URL not set — refusing to start." >&2
    echo "[supervisor]   set it in .env (e.g. redis://localhost:6379/0)" >&2
    exit 2
fi

# ─── Pre-flight cleanup ────────────────────────────────────────────
# Why this exists: previously launching the supervisor while a stale
# beat/worker/uvicorn was already running (e.g. after a kill -9, a
# previous supervisor that crashed, or just plain double-start) caused
# two problems:
#   1. ``celery beat --pidfile celerybeat.pid`` exits 73 ("already
#      running") on every restart attempt → 1-minute infinite restart
#      loop while the orphan beat keeps scheduling tasks.
#   2. Multiple ``celery worker`` families competing for the same redis
#      queue → wasted memory + confusing logs.
# Fix: before launching anything, kill any process listed in our
# supervisor-owned pid files AND celery's own pidfile, then remove the
# files. After this, the new run_child loops own a clean slate.
preflight_cleanup() {
    local files=(
        "$LOG_DIR/.pgbouncer.pid"
        "$LOG_DIR/.uvicorn.pid"
        "$LOG_DIR/.celery-worker.pid"
        "$LOG_DIR/.celery-beat.pid"
        "$LOG_DIR/celerybeat.pid"
    )
    local killed=0
    for f in "${files[@]}"; do
        [ -f "$f" ] || continue
        local pid
        pid=$(tr -d '[:space:]' < "$f")
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            echo "[$(date '+%H:%M:%S')] supervisor: preflight killing stale $f pid=$pid"
            kill -TERM "$pid" 2>/dev/null || true
            killed=$((killed + 1))
        fi
        rm -f "$f"
    done
    # Also nuke any leftover hermes_cloud python processes we didn't
    # know about (e.g. previous shell ran `uv run uvicorn` directly
    # without supervisor). Match on the full argv signature, not just
    # the binary, so we don't kill unrelated celery/uvicorn instances.
    local orphans
    orphans=$(pgrep -f "celery -A hermes_cloud\.app\.celery_app|uvicorn hermes_cloud\.app\.dev_server|pgbouncer pgbouncer/pgbouncer\.ini" 2>/dev/null || true)
    if [ -n "$orphans" ]; then
        echo "[$(date '+%H:%M:%S')] supervisor: preflight killing orphan hermes_cloud pids: $orphans"
        # shellcheck disable=SC2086
        kill -TERM $orphans 2>/dev/null || true
        killed=$((killed + 1))
    fi
    if [ "$killed" -gt 0 ]; then
        # Give graceful TERM 3s before next step (run_child will spawn
        # fresh children). Without this, the new celery beat sometimes
        # races the dying one for the pidfile.
        sleep 3
        # Force-kill anything that ignored TERM.
        for f in "${files[@]}"; do
            [ -f "$f" ] || continue
            local pid
            pid=$(tr -d '[:space:]' < "$f")
            [ -n "$pid" ] && kill -KILL "$pid" 2>/dev/null || true
            rm -f "$f"
        done
        local stragglers
        stragglers=$(pgrep -f "celery -A hermes_cloud\.app\.celery_app|uvicorn hermes_cloud\.app\.dev_server|pgbouncer pgbouncer/pgbouncer\.ini" 2>/dev/null || true)
        # shellcheck disable=SC2086
        [ -n "$stragglers" ] && kill -KILL $stragglers 2>/dev/null || true
    fi
}
preflight_cleanup

declare -a child_pids=()
# Child names — must match the run_child "<name>" calls below. shutdown()
# uses them to locate each child's *real* pid in $LOG_DIR/.<name>.pid.
child_names=(pgbouncer uvicorn celery-worker celery-beat)
stopping=0

# Echo each supervised child's real pid, one per line, skipping any that
# already exited. run_child records the exec'd process pid (pgbouncer /
# uvicorn / celery — not the wrapper subshell) in $LOG_DIR/.<name>.pid.
real_child_pids() {
    local n f pid
    for n in "${child_names[@]}"; do
        f="$LOG_DIR/.${n}.pid"
        [ -f "$f" ] || continue
        pid=$(tr -d '[:space:]' < "$f")
        [ -n "$pid" ] && echo "$pid"
    done
}

shutdown() {
    local pid rpid alive
    stopping=1
    echo "[$(date '+%H:%M:%S')] supervisor: shutting down (${#child_pids[@]} children)"
    # Kill the restart-loop wrappers FIRST so they don't relaunch a child
    # we're about to TERM. $child_pids holds run_child subshell pids;
    # killing those alone does NOT reach the exec'd long-lived process —
    # which is why the real pids are TERM'd separately right after.
    for pid in "${child_pids[@]}"; do
        [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null || true
    done
    # TERM the actual processes (pgbouncer / uvicorn / celery).
    for rpid in $(real_child_pids); do
        kill -TERM "$rpid" 2>/dev/null || true
    done
    # give them up to 15s to exit gracefully
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
        alive=0
        for rpid in $(real_child_pids); do
            kill -0 "$rpid" 2>/dev/null && alive=1
        done
        [ "$alive" = "0" ] && break
        sleep 1
    done
    # force-kill stragglers — real processes, then wrappers
    for rpid in $(real_child_pids); do
        kill -KILL "$rpid" 2>/dev/null || true
    done
    for pid in "${child_pids[@]}"; do
        [ -n "$pid" ] && kill -KILL "$pid" 2>/dev/null || true
    done
    exit 0
}
trap shutdown TERM INT

# Run one child in a restart-loop in the background. Each loop body
# launches the command, waits for it, then sleeps an exponential
# backoff (reset if the run lasted >60s).
run_child() {
    local name="$1"
    local log="$2"
    shift 2
    local backoff=2
    local max_backoff=60
    while [ "$stopping" = "0" ]; do
        local start_ts
        start_ts=$(date +%s)
        echo "[$(date '+%H:%M:%S')] supervisor: starting $name" | tee -a "$log"
        # `exec` so the PID we get IS the child, not a wrapper shell.
        ( exec "$@" ) >>"$log" 2>&1 &
        local pid=$!
        # Publish this child's pid back to the parent's tracking array
        # via a tmpfile keyed by name — `wait` below blocks on this pid.
        echo "$pid" > "$LOG_DIR/.${name}.pid"
        wait "$pid"
        local exit_code=$?
        rm -f "$LOG_DIR/.${name}.pid"
        if [ "$stopping" = "1" ]; then
            return 0
        fi
        local end_ts ran_for
        end_ts=$(date +%s)
        ran_for=$((end_ts - start_ts))
        echo "[$(date '+%H:%M:%S')] supervisor: $name exited code=$exit_code after ${ran_for}s; restart in ${backoff}s" | tee -a "$log"
        sleep "$backoff"
        if [ "$ran_for" -gt 60 ]; then
            backoff=2
        elif [ "$backoff" -lt "$max_backoff" ]; then
            backoff=$((backoff * 2))
            [ "$backoff" -gt "$max_backoff" ] && backoff=$max_backoff
        fi
    done
}

# pgbouncer launches first: uvicorn and celery open their PG connection
# pools during startup, so the proxy must already be accepting clients.
# It's a normal supervised child — crash-restarted with the same backoff.
run_child "pgbouncer" "$LOG_DIR/pgbouncer.log" \
    "$PGBOUNCER_BIN" pgbouncer/pgbouncer.ini &
child_pids+=($!)

# Block until pgbouncer accepts connections on :6432 (max ~15s) before
# spawning the app. A miss isn't fatal — pool_pre_ping + pool retries
# tolerate a late start — but waiting avoids noisy startup errors.
pgbouncer_ready=0
for _ in $(seq 1 30); do
    # bash /dev/tcp probe — no `nc` dependency. A refused connection
    # (pgbouncer not listening yet) fails fast; the redirect error is
    # swallowed so only a real "ready" breaks the loop.
    if (: < /dev/tcp/127.0.0.1/6432) 2>/dev/null; then
        pgbouncer_ready=1
        break
    fi
    sleep 0.5
done
if [ "$pgbouncer_ready" = "1" ]; then
    echo "[$(date '+%H:%M:%S')] supervisor: pgbouncer ready on :6432"
else
    echo "[$(date '+%H:%M:%S')] supervisor: WARNING pgbouncer not ready after 15s — starting app anyway" >&2
fi

# Launch the remaining children as background subshells. The PID we
# track in $child_pids is the subshell's pid — killing it cascades to
# the `exec`'d grandchild via the same process group.
# Direct .venv/bin/* invocation (not `uv run …`): uv spawns the target
# binary as a child process, so when `exec` replaces the subshell with
# `uv run`, our $! points at the uv wrapper, not the python process
# doing the actual work. If uv ever exits before its child (or vice
# versa), `wait $!` returns while the orphan keeps running — supervisor
# then "restarts" while the orphan still holds the celery pidfile,
# causing the infinite exit-73 loop we just fixed in preflight_cleanup.
# Direct invocation eliminates the wrapper entirely.
run_child "uvicorn" "$LOG_DIR/uvicorn.log" \
    .venv/bin/uvicorn hermes_cloud.app.dev_server:app \
        --host 0.0.0.0 --port "$PORT" &
child_pids+=($!)

run_child "celery-worker" "$LOG_DIR/celery-worker.log" \
    .venv/bin/celery -A hermes_cloud.app.celery_app worker \
        -l info -c 2 --without-heartbeat &
child_pids+=($!)

run_child "celery-beat" "$LOG_DIR/celery-beat.log" \
    .venv/bin/celery -A hermes_cloud.app.celery_app beat \
        -l info \
        --schedule "$LOG_DIR/celerybeat-schedule" \
        --pidfile "$LOG_DIR/celerybeat.pid" &
child_pids+=($!)

echo "[$(date '+%H:%M:%S')] supervisor: spawned pgbouncer(${child_pids[0]}) uvicorn(${child_pids[1]}) worker(${child_pids[2]}) beat(${child_pids[3]})"
echo "[$(date '+%H:%M:%S')] supervisor: tail -f $LOG_DIR/{pgbouncer,uvicorn,celery-worker,celery-beat}.log"

# Park here until one of the run_child loops exits (only happens on
# shutdown — they restart their child on crash).
wait

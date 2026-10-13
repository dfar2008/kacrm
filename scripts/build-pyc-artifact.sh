#!/usr/bin/env bash
# Build a sourceless-.pyc deploy artifact for hermes_cloud/ (our IP).
#
# Option A (per 2026-05-29 decision): only hermes_cloud/ is compiled to
# .pyc. The hermes/ submodule stays plaintext .py because it is upstream
# code AND because its tool/plugin discovery globs ``*.py`` + AST-parses
# source at runtime — sourceless .pyc would silently zero out the tool
# registry (verified: 31 tools -> 0). See
# docs/superpowers/specs/ (pyc deploy) for the full rationale.
#
# Output: dist/deploy/hermes_cloud/  — a sourceless package tree (only
# .pyc, no .py) ready to rsync onto the server in place of the plaintext
# hermes_cloud/.
#
# IMPORTANT: .pyc bytecode is tied to the CPython *minor* version (magic
# number). The target server venv is python 3.11.x; this script refuses
# to build with anything other than 3.11 so the artifact will load there.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

PY="${PYC_BUILD_PYTHON:-$repo_root/.venv/bin/python}"
SRC="$repo_root/hermes_cloud"
OUT="$repo_root/dist/deploy"
STAGE="$OUT/hermes_cloud"

# ── 1. Guard: python minor must be 3.11 (server venv is 3.11.x) ───────
ver="$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$ver" != "3.11" ]]; then
    echo "FATAL: build python is $ver, server venv is 3.11.x." >&2
    echo "       .pyc magic must match. Use a 3.11 interpreter via PYC_BUILD_PYTHON=." >&2
    exit 2
fi
magic="$("$PY" -c 'import importlib.util,binascii; print(binascii.hexlify(importlib.util.MAGIC_NUMBER).decode())')"
echo "==> build python: $("$PY" --version) (3.11 OK)  pyc magic: $magic"
echo "    (server must run a 3.11.x venv; magic is identical across 3.11.x)"

# ── 2. Stage a clean copy of hermes_cloud/ ────────────────────────────
echo "==> staging $SRC -> $STAGE"
rm -rf "$OUT"
mkdir -p "$OUT"
rsync -a --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo' "$SRC" "$OUT/"

# ── 3. Compile to .pyc (legacy in-place layout) ───────────────────────
# -b writes foo.pyc next to foo.py (sourceless layout, not __pycache__).
# Optimization left at level 0 so asserts/docstrings behave as in dev.
echo "==> compiling to .pyc"
"$PY" -m compileall -b -q -j 4 "$STAGE"

# ── 4. Strip all .py — sourceless. Keep .pyc + data files (.md/.json/…) ─
echo "==> stripping .py (sourceless)"
find "$STAGE" -name '*.py' -delete
py_left="$(find "$STAGE" -name '*.py' | wc -l | tr -d ' ')"
pyc_made="$(find "$STAGE" -name '*.pyc' | wc -l | tr -d ' ')"
if [[ "$py_left" != "0" ]]; then
    echo "FATAL: $py_left .py files remain after strip — not sourceless." >&2
    exit 3
fi
echo "    .py remaining: $py_left (must be 0)   .pyc produced: $pyc_made"

# ── 5. Self-check: import the app factory from the sourceless tree ─────
# Bypass the editable finder (-S) and point sys.path at the staged tree
# + hermes (kept .py) + venv site-packages, exactly like the server.
sp="$repo_root/.venv/lib/python3.11/site-packages"
echo "==> import self-check (from $OUT)"
# NOTE: do not import app.dev_server here — it calls create_dev_app() at
# module load, which requires DB env. We only need to prove sourceless
# .pyc *imports*; runtime boot is verified on the server post-deploy.
"$PY" -S -c "
import sys
sys.path[:0] = ['$repo_root/hermes', '$OUT', '$sp']
import hermes_cloud.app.main as m            # FastAPI app factory (no side effect at import)
import hermes_cloud.app.copilot.routes       # copilot dispatch path
import hermes_cloud.app.proxy.dispatch       # cloud->hermes dispatch
assert m.__file__.endswith('.pyc'), m.__file__
print('    OK  app.main <-', m.__file__)
print('    OK  app.copilot.routes, app.proxy.dispatch import clean from .pyc')
"

echo
echo "==> artifact ready: $STAGE"
echo "    rsync this dir onto the server in place of plaintext hermes_cloud/."

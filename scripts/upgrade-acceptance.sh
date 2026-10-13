#!/usr/bin/env bash
# Upgrade-acceptance gate — run BEFORE merging any hermes submodule bump.
#
# hermes upgrades frequently; every bump risks silently dropping a cloud hook
# (v0.18 re-homed the platform adapters; earlier bumps broke sqlite persistence
# and set_turn_meta). This gate asserts the cloud↔hermes CONTRACTS still hold
# on the NEW hermes, so a regression goes red HERE instead of in production.
#
# Non-destructive: only imports + runs tests against the current tree. (Patch
# idempotency — "all 7 patches still apply to the new vanilla, 0 rejects" — is
# verified separately by scripts/regen-hermes-patches.sh + the reset→re-apply
# step in the upgrade runbook; see hermes-patches/UPGRADING.md.)
#
# Usage:
#   ./scripts/upgrade-acceptance.sh
#
# Exit non-zero on ANY failure → do not merge the upgrade.
set -uo pipefail

repo_root="$(git rev-parse --show-toplevel)"
hermes_dir="$repo_root/hermes"
venv_py="$repo_root/.venv/bin/python"
export PYTHONPATH="$hermes_dir:$repo_root"
# Keep hermes' HERMES_HOME warning quiet + writes off the real profiles.
export HERMES_HOME="${HERMES_HOME:-$repo_root/.upgrade-acceptance-home}"
mkdir -p "$HERMES_HOME"

fail=0
step() { echo; echo "── $1 ────────────────────────────────────────────"; }
check() { if [ "$1" -ne 0 ]; then echo "❌ FAILED: $2"; fail=1; else echo "✅ $2"; fi; }

echo "======================================================================"
echo " Hermes upgrade-acceptance gate"
echo " hermes @ $(git -C "$hermes_dir" rev-parse --short HEAD 2>/dev/null || echo '?')"
echo "======================================================================"

# 1. Cross-surface acceptance suite (SPA / IM / Agent / persistence / clarify).
step "1/3 cross-surface acceptance suite (test_upgrade_acceptance_*.py)"
"$venv_py" -m pytest "$repo_root"/tests/hermes_cloud/test_upgrade_acceptance_*.py -q
check "$?" "acceptance suite (patches/adapters/agent/persistence/clarify)"

# 2. SessionDB persistence contract (guards the 'messages vanish on refresh' class).
step "2/3 SessionDB persistence contract"
"$venv_py" -m pytest "$repo_root"/tests/hermes_cloud/test_session_db_contract.py -q 2>/dev/null
check "$?" "SessionDB contract"

# 3. Key hermes unit tests our patches touch (clarify + turn-context nudge hydration).
step "3/3 patched-path hermes unit tests"
( cd "$hermes_dir" && "$venv_py" -m pytest \
    tests/tools/test_clarify_tool.py \
    tests/agent/test_turn_context.py \
    tests/gateway/test_multiplex_channel_push.py -q )
check "$?" "clarify + turn_context + multiplex channel-push unit tests"

echo
echo "======================================================================"
if [ "$fail" -ne 0 ]; then
    echo " ❌ UPGRADE ACCEPTANCE FAILED — do NOT merge the hermes bump."
    echo "    A cloud↔hermes contract regressed. Fix the re-homed hook /"
    echo "    signature before merging (see hermes-patches/README.md)."
    exit 1
fi
echo " ✅ UPGRADE ACCEPTANCE PASSED — cloud contracts intact on this hermes."
echo "======================================================================"

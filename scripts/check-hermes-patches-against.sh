#!/usr/bin/env bash
# Pre-upgrade triage: do all hermes-patches/*.patch still apply cleanly
# against a given upstream tag/SHA?
#
# Usage:
#   ./scripts/check-hermes-patches-against.sh v2026.5.21
#   ./scripts/check-hermes-patches-against.sh main
#
# Output (per-patch):
#   OK     <name>      → applies clean against the new base
#   FAIL   <name>      → conflict; full runbook needed
#
# Exit codes:
#   0  every patch applies cleanly → 5-minute pointer bump path
#   1  one or more patches conflict → runbook 8-stage required
#   2  bad usage / missing target / shallow clone failure
#
# What it does:
#   1. Clones upstream hermes into /tmp/hermes-patch-check-<tag>.
#   2. Checks out the requested tag/SHA.
#   3. For each patches/*.patch, runs `git apply --check`.
#   4. Reports the verdict.
#
# Why a separate spike clone instead of touching live `hermes/`:
#   The live submodule has all our patches applied to the OLD pin. A
#   forward-check against a NEW tag from inside the live tree would be
#   misleading (it'd report "drift" for every patch since the file
#   contents already differ). The spike is the only honest answer.
#
# This script is read-only — never writes outside /tmp.

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <tag-or-sha-or-branch>" >&2
    echo "Example: $0 v2026.5.21" >&2
    exit 2
fi

target="$1"
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
patches_dir="$repo_root/hermes-patches"

if ! [[ -d "$patches_dir" ]]; then
    echo "no hermes-patches/ at $patches_dir" >&2
    exit 2
fi

# Reuse a previous spike clone for the same target if it exists, else
# fresh-clone. Clones are full (not shallow) so any tag/SHA resolves.
spike="/tmp/hermes-patch-check-$(printf '%s' "$target" | tr '/' '_')"

if [[ -d "$spike/.git" ]]; then
    echo "==> reusing spike clone at $spike"
    git -C "$spike" fetch --quiet --tags origin || true
else
    echo "==> cloning hermes upstream into $spike (this takes ~1 min)"
    rm -rf "$spike"
    git clone --quiet https://github.com/NousResearch/hermes-agent "$spike" \
        || { echo "clone failed" >&2; exit 2; }
fi

if ! git -C "$spike" rev-parse --quiet --verify "$target" >/dev/null; then
    echo "==> $target not found in upstream — fetching tags + branches" >&2
    git -C "$spike" fetch --quiet --tags origin
    git -C "$spike" fetch --quiet origin
    if ! git -C "$spike" rev-parse --quiet --verify "$target" >/dev/null; then
        echo "still can't resolve $target" >&2
        exit 2
    fi
fi

echo "==> checking out $target"
git -C "$spike" checkout --quiet "$target"
git -C "$spike" reset --hard --quiet "$target"
sha="$(git -C "$spike" rev-parse HEAD)"
desc="$(git -C "$spike" describe --tags --always)"
echo "==> $desc ($sha)"
echo

# Walk patches in dependency order (filename sort). For each, try a
# forward apply --check against the spike's current state. Don't actually
# apply — we just want the verdict. Patches that depend on earlier
# patches (e.g. 19 layers on top of 14) DO need the earlier patch
# applied for the check to be honest, so apply-as-we-go and roll back
# on failure.

fail_count=0
applied=()

cleanup() {
    # Reverse-apply everything we touched, in reverse order, to leave
    # the spike clone reusable for the next run.
    for ((i=${#applied[@]}-1; i>=0; i--)); do
        git -C "$spike" apply --reverse "${applied[i]}" 2>/dev/null || true
    done
}
trap cleanup EXIT

shopt -s nullglob
for p in "$patches_dir"/*.patch; do
    name="$(basename "$p")"
    if git -C "$spike" apply --check "$p" 2>/dev/null; then
        # Apply for real so subsequent dependent patches can layer.
        if git -C "$spike" apply "$p" 2>/dev/null; then
            applied+=("$p")
            printf '  OK     %s\n' "$name"
        else
            # check passed but apply failed — extremely rare, treat as fail.
            printf '  FAIL   %s (check ok but apply errored)\n' "$name"
            fail_count=$((fail_count + 1))
        fi
    else
        printf '  FAIL   %s\n' "$name"
        # Show first few error lines for triage.
        # Capture first to avoid SIGPIPE (head closing early) aborting the
        # loop under `set -euo pipefail`.
        err="$(git -C "$spike" apply --check "$p" 2>&1 || true)"
        printf '%s\n' "$err" | sed 's/^/         /' | head -3 || true
        fail_count=$((fail_count + 1))
    fi
done
shopt -u nullglob

echo
if (( fail_count == 0 )); then
    echo "==> all patches apply cleanly against $desc"
    echo "    next upgrade is a 5-minute pointer bump."
    exit 0
else
    echo "==> $fail_count patch(es) conflict against $desc"
    echo "    full runbook needed: docs/2026-10-12-hermes-v0.21.6-upgrade.md"
    exit 1
fi

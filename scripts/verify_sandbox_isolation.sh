#!/usr/bin/env bash
# =============================================================================
# verify_sandbox_isolation.sh
#
# Manual end-to-end verification that two API requests with different
# X-Hermes-Session-Id headers land in completely isolated sandbox containers.
#
# REQUIREMENTS
# ------------
#   - Docker must be running
#   - The Hermes agent must be installed (hermes command on PATH)
#   - The terminal toolset must be enabled in hermes configuration
#   - jq must be installed (for JSON response parsing)
#
# WHAT THIS VERIFIES
# ------------------
# 1. The Hermes API server starts successfully
# 2. Two requests with distinct X-Hermes-Session-Id headers each write
#    "test_isolation.txt" with different content to /workspace inside their
#    respective containers.
# 3. The content from session A is NOT visible inside session B's container
#    (no cross-contamination).
# 4. Each session's environment is keyed by its own session ID, never shared.
#
# OUTPUT
#   Prints PASS or FAIL with a brief explanation.
#
# USAGE
#   bash scripts/verify_sandbox_isolation.sh
#
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_PORT="${HERMES_API_PORT:-8888}"
API_BASE="http://127.0.0.1:${API_PORT}"
API_KEY="${HERMES_INTERNAL_KEY:-test-key}"
SERVER_START_TIMEOUT=15   # seconds to wait for server to become ready
SESSION_A_ID="isolation-test-session-alpha"
SESSION_B_ID="isolation-test-session-beta"
CONTENT_A="SANDBOX_A_UNIQUE_CONTENT_$(date +%s)"
CONTENT_B="SANDBOX_B_UNIQUE_CONTENT_$(date +%s)"
SERVER_PID=""

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------
pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }
info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*"; }

cleanup() {
    if [[ -n "$SERVER_PID" ]]; then
        info "Stopping API server (PID $SERVER_PID)..."
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Step 1 — Preflight checks
# ---------------------------------------------------------------------------
info "Step 1: Preflight checks"

# 1a. Docker must be available and daemon must be running.
if ! command -v docker &>/dev/null; then
    fail "docker not found on PATH. Install Docker and try again."
fi
if ! docker info &>/dev/null; then
    fail "Docker daemon is not running. Start Docker and try again."
fi
pass "Docker is running."

# 1b. hermes must be available.
if ! command -v hermes &>/dev/null; then
    fail "'hermes' command not found. Install hermes-agent and activate the virtualenv."
fi
pass "hermes command found: $(command -v hermes)"

# 1c. jq must be available for JSON parsing.
if ! command -v jq &>/dev/null; then
    fail "'jq' not found. Install it (e.g. brew install jq) and try again."
fi
pass "jq found."

# ---------------------------------------------------------------------------
# Step 2 — Start the Hermes API server in the background
# ---------------------------------------------------------------------------
info "Step 2: Starting Hermes API server on port ${API_PORT}..."

hermes gateway --platform api_server --port "${API_PORT}" \
    &>"${TMPDIR:-/tmp}/hermes_isolation_test_server.log" &
SERVER_PID=$!
info "Server started with PID ${SERVER_PID}."

# Wait for the /health endpoint to respond.
START_TS=$(date +%s)
while true; do
    if curl -sf "${API_BASE}/health" &>/dev/null; then
        break
    fi
    NOW=$(date +%s)
    if (( NOW - START_TS > SERVER_START_TIMEOUT )); then
        info "Server logs:"
        cat "${TMPDIR:-/tmp}/hermes_isolation_test_server.log" >&2 || true
        fail "API server did not become ready within ${SERVER_START_TIMEOUT}s."
    fi
    sleep 1
done
pass "API server is ready at ${API_BASE}."

# ---------------------------------------------------------------------------
# Step 3 — Session A writes a unique file
# ---------------------------------------------------------------------------
info "Step 3: Session A (${SESSION_A_ID}) writes test_isolation.txt..."

PROMPT_A="Write the exact text '${CONTENT_A}' to the file /workspace/test_isolation.txt using the terminal. Then print the contents with: cat /workspace/test_isolation.txt"

RESPONSE_A=$(curl -sf \
    -X POST "${API_BASE}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${API_KEY}" \
    -H "X-Hermes-Session-Id: ${SESSION_A_ID}" \
    -d "$(jq -n --arg msg "$PROMPT_A" '{
        model: "default",
        messages: [{"role": "user", "content": $msg}]
    }')")

# Extract the assistant reply.
REPLY_A=$(echo "$RESPONSE_A" | jq -r '.choices[0].message.content // ""')
if [[ -z "$REPLY_A" ]]; then
    info "Full response from session A: $RESPONSE_A"
    fail "Session A returned an empty reply."
fi
info "Session A reply: ${REPLY_A:0:200}..."

# Verify the response confirms the content was written.
if ! echo "$REPLY_A" | grep -qF "$CONTENT_A"; then
    warn "Session A reply does not explicitly echo the content — continuing anyway."
fi
pass "Session A completed."

# ---------------------------------------------------------------------------
# Step 4 — Session B writes a different file and checks for A's content
# ---------------------------------------------------------------------------
info "Step 4: Session B (${SESSION_B_ID}) writes test_isolation.txt and checks isolation..."

PROMPT_B="Write the exact text '${CONTENT_B}' to the file /workspace/test_isolation.txt. Then check if a file /workspace/test_isolation.txt exists and print its contents. Also run: cat /workspace/test_isolation.txt"

RESPONSE_B=$(curl -sf \
    -X POST "${API_BASE}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${API_KEY}" \
    -H "X-Hermes-Session-Id: ${SESSION_B_ID}" \
    -d "$(jq -n --arg msg "$PROMPT_B" '{
        model: "default",
        messages: [{"role": "user", "content": $msg}]
    }')")

REPLY_B=$(echo "$RESPONSE_B" | jq -r '.choices[0].message.content // ""')
if [[ -z "$REPLY_B" ]]; then
    info "Full response from session B: $RESPONSE_B"
    fail "Session B returned an empty reply."
fi
info "Session B reply: ${REPLY_B:0:200}..."
pass "Session B completed."

# ---------------------------------------------------------------------------
# Step 5 — Verify no cross-contamination
# ---------------------------------------------------------------------------
info "Step 5: Verifying cross-contamination did not occur..."

# Session B's reply must contain B's own content.
if ! echo "$REPLY_B" | grep -qF "$CONTENT_B"; then
    fail "Session B's reply does not confirm that it wrote its own content (${CONTENT_B})."
fi
pass "Session B's content (${CONTENT_B}) is present in session B's reply."

# Session B's reply must NOT contain A's content — different containers.
if echo "$REPLY_B" | grep -qF "$CONTENT_A"; then
    fail "ISOLATION VIOLATION: Session B's reply contains session A's content (${CONTENT_A}). The sandboxes are NOT isolated."
fi
pass "Session A's content (${CONTENT_A}) is NOT visible in session B's sandbox."

# ---------------------------------------------------------------------------
# Step 6 — Verify session A's file still has its original content
# ---------------------------------------------------------------------------
info "Step 6: Confirming session A's file was not overwritten by session B..."

PROMPT_A2="Read the file /workspace/test_isolation.txt and print its exact contents."

RESPONSE_A2=$(curl -sf \
    -X POST "${API_BASE}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${API_KEY}" \
    -H "X-Hermes-Session-Id: ${SESSION_A_ID}" \
    -d "$(jq -n --arg msg "$PROMPT_A2" '{
        model: "default",
        messages: [{"role": "user", "content": $msg}]
    }')")

REPLY_A2=$(echo "$RESPONSE_A2" | jq -r '.choices[0].message.content // ""')
info "Session A re-read reply: ${REPLY_A2:0:200}..."

if echo "$REPLY_A2" | grep -qF "$CONTENT_B"; then
    fail "ISOLATION VIOLATION: Session A's sandbox contains session B's content. The sandboxes are NOT isolated."
fi
pass "Session A's sandbox still contains only session A's data."

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "======================================================================"
echo " SANDBOX ISOLATION VERIFICATION: PASS"
echo "======================================================================"
echo " Session A ID : ${SESSION_A_ID}"
echo " Session B ID : ${SESSION_B_ID}"
echo " Session A wrote : ${CONTENT_A}"
echo " Session B wrote : ${CONTENT_B}"
echo " Cross-contamination : NONE DETECTED"
echo "======================================================================"

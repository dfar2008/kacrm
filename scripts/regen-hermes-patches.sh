#!/usr/bin/env bash
# Regenerate hermes-patches/*.patch from the current hermes/ working tree.
#
# Usage:
#   ./scripts/regen-hermes-patches.sh
#
# The 7 patches split by upgrade-conflict risk (see hermes-patches/README.md):
#   10-hermes-capabilities   — every NEW file (additive; never conflicts)
#   15-prune-upstream-skills — deletions of unused upstream bundled skills
#   20-gateway-cloud-hooks   — modifications to gateway/* upstream files
#   25-cron-cloud-hooks      — modifications to cron/* upstream files
#   30-agent-cloud-hooks     — modifications to agent/* + run_agent + cli
#   40-hermes-state-cloud-db — modifications to hermes_state.py
#   50-upstream-tests        — modifications to upstream tests/*
#
# Workflow: edit hermes/ files, run this, commit the updated patch files.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
hermes_dir="$repo_root/hermes"
patches_dir="$repo_root/hermes-patches"
cd "$hermes_dir"

# Upstream files we modify, grouped per patch. Anything modified outside
# these groups is a regen error — add it to a group or make a new patch.
# v0.18.0 moved the feishu/email/dingtalk/wecom platform adapters out of
# gateway/platforms/*.py into the plugin layout plugins/platforms/<p>/adapter.py
# (#41112). Our hooks re-homed there; api_server/weixin stayed in gateway/platforms/.
# multiplex A-group additions (2026-07-24): loop-blocking offloads land in
# base.py (delivery ledger); HERMES_HOME per-call resolution lands in
# channel_directory/hooks/mirror/sticker_cache/feishu_comment_rules.
# multiplex B3 addition (2026-07-24): authz_mixin per-turn DM-allow (B3b).
gateway_files=(gateway/run.py gateway/stream_consumer.py gateway/slash_commands.py gateway/platforms/api_server.py gateway/platforms/api_server_runs.py gateway/platforms/weixin.py gateway/platforms/base.py gateway/channel_directory.py gateway/hooks.py gateway/mirror.py gateway/sticker_cache.py gateway/authz_mixin.py plugins/platforms/feishu/adapter.py plugins/platforms/feishu/feishu_comment_rules.py plugins/platforms/email/adapter.py plugins/platforms/dingtalk/adapter.py plugins/platforms/wecom/adapter.py gateway/platforms/api_server_openai_routes.py gateway/run_adapters.py gateway/run_inbound.py gateway/run_turn.py gateway/run_turn_runner.py gateway/run_turn_runner_clarify_delivery.py gateway/slash_commands_session.py gateway/stream_consumer_fallback.py plugins/platforms/wecom/media.py)
cron_files=(cron/scheduler.py cron/scheduler_delivery.py)
# v0.15.0's run_agent.py refactor (16k→4.6k) moved the tool-dispatch loop
# into agent/tool_executor.py, so the surface-ACL hard-check now lives there
# instead of run_agent.py. run_agent.py stays listed (harmless if unmodified).
# multiplex A-group additions (2026-07-24): HERMES_HOME per-call resolution in
# tools/skills_sync.py + tools/environments/{singularity,modal}.py.
agent_files=(agent/prompt_builder.py agent/conversation_loop.py agent/skill_utils.py agent/tool_executor.py agent/auxiliary_client.py run_agent.py hermes_cli/tools_config.py hermes_cli/config.py hermes_cli/config_defaults.py tools/clarify_tool.py tools/web_tools.py tools/skill_manager_tool.py tools/skills_sync.py tools/environments/singularity.py tools/environments/modal.py agent/system_prompt.py agent/turn_context.py toolsets.py agent/chat_completion_helpers.py agent/message_metadata.py agent/session_persistence.py agent/turn_final_response.py agent/turn_request_assembly.py agent/turn_response_check.py agent/turn_truncation.py agent/turn_usage.py tools/skill_manager_guards.py)
state_files=(hermes_state.py hermes_state_messages.py hermes_state_repair.py)
# multiplex A-group addition (2026-07-24): test_feishu_comment_rules.py patch
# targets updated for the per-path cache seam.
test_files=(tests/agent/test_prompt_builder.py tests/conftest.py tests/tools/test_cronjob_tools.py tests/gateway/test_email.py tests/cron/test_scheduler.py tests/tools/test_web_providers.py tests/tools/test_web_tools_config.py tests/gateway/test_api_server_toolset.py tests/tools/test_browser_console.py tests/gateway/test_weixin.py tests/agent/test_turn_context.py tests/gateway/test_feishu.py tests/gateway/test_feishu_comment_rules.py tests/agent/test_system_prompt.py)

# Stage everything so new (untracked) files are visible to ``git diff --cached``.
git add -A

# --no-renames is REQUIRED: our additive files include empty ``__init__.py``
# package markers, and 15-prune deletes upstream skills that also contain
# empty ``__init__.py`` files. With rename detection on (git's default), an
# added empty file gets paired with a deleted empty file as a 100%-rename
# (R100), so it falls out of BOTH --diff-filter=A (patch 10 loses the add)
# and --diff-filter=D (patch 15 loses the delete). --no-renames forces the
# honest add/delete classification.
git diff --cached --no-renames --diff-filter=A                          > "$patches_dir/10-hermes-capabilities.patch"
# --binary: pruned upstream skills include binary assets (PDF templates,
# images) — without it git apply rejects the deletion patch.
git diff --cached --no-renames --binary --diff-filter=D -- skills/      > "$patches_dir/15-prune-upstream-skills.patch"
git diff --cached --no-renames --diff-filter=M -- "${gateway_files[@]}"  > "$patches_dir/20-gateway-cloud-hooks.patch"
git diff --cached --no-renames --diff-filter=M -- "${cron_files[@]}"     > "$patches_dir/25-cron-cloud-hooks.patch"
git diff --cached --no-renames --diff-filter=M -- "${agent_files[@]}"    > "$patches_dir/30-agent-cloud-hooks.patch"
git diff --cached --no-renames --diff-filter=M -- "${state_files[@]}"    > "$patches_dir/40-hermes-state-cloud-db.patch"
git diff --cached --no-renames --diff-filter=M -- "${test_files[@]}"     > "$patches_dir/50-upstream-tests.patch"

# Guard: every modified upstream file must belong to a group, else its
# changes would silently leak nowhere.
grouped=$(printf '%s\n' "${gateway_files[@]}" "${cron_files[@]}" "${agent_files[@]}" "${state_files[@]}" "${test_files[@]}" | sort)
modified=$(git diff --cached --no-renames --diff-filter=M --name-only | sort)
ungrouped=$(comm -23 <(echo "$modified") <(echo "$grouped") || true)

# Guard: deletions only ever prune bundled skills. A deletion anywhere
# else means an upstream file got removed by accident — refuse rather
# than silently bake it into 15-prune-upstream-skills.patch.
deleted_outside=$(git diff --cached --no-renames --diff-filter=D --name-only | grep -v '^skills/' || true)

git reset >/dev/null

if [[ -n "${ungrouped// /}" ]]; then
    echo "ERROR: modified upstream files not in any patch group:" >&2
    echo "$ungrouped" >&2
    echo "Add them to a group in $0 or create a new patch." >&2
    exit 1
fi

if [[ -n "${deleted_outside// /}" ]]; then
    echo "ERROR: files deleted outside skills/ — refusing to regen:" >&2
    echo "$deleted_outside" >&2
    exit 1
fi

echo "Regenerated 7 patches under $patches_dir:"
ls -1 "$patches_dir"/*.patch

# Guard against silent content regression (see runbooks/hermes-patches-regen-checklist.md).
# Anchor check: shipped skills/tools must still appear in patch 10. If regen ran
# with a stale submodule working tree the diff would silently drop them (208c4fe 事故).
echo
echo "Verifying patch content anchors..."
if ! "$repo_root/scripts/verify-patch-content.sh"; then
    echo
    echo "========================================================================"
    echo "PATCHES ON DISK ARE INCOMPLETE — DO NOT COMMIT"
    echo "verify-patch-content.sh detected missing anchors."
    echo "Likely cause: submodule working tree was missing prior patch content."
    echo "Inspect 'git diff hermes-patches/' and re-read docs/runbooks/hermes-patches-regen-checklist.md."
    echo "========================================================================"
    exit 1
fi

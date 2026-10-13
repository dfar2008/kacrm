# hermes-patches

Local patches applied on top of the `hermes` submodule at hermes_cloud
startup. The submodule tracks upstream
[`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent)
— currently **v0.21.6** (SHA `818c13be1`).

## Why

The `hermes` submodule points at upstream — we can't bump its pointer
to a local SHA (other machines cloning us would fail to resolve it),
and we can't push our fixes to upstream main. So our local changes live
as patch files here, and `hermes_cloud` reapplies them idempotently at
every boot.

## The 7 patches

They split by **upgrade-conflict risk**:

| File | Kind | Contents |
|------|------|----------|
| `10-hermes-capabilities.patch` | **purely additive** (106 new files) | The cloud-grown hermes capabilities: `crm/` (CRM stack — models, save/edit/list/detail queries, dispatch, approval, projections, i18n), `action_gateway/`, `memory/`, `trust/`, `tools/crm/` + `tools/crm_tools.py` + `tools/{surface_acl,scope}.py`, `agent/overlay_resolver.py`, the bundled `skills/crm-*`, `plugins/cloud-approval/`, and their tests. Upstream has none of these paths, so this patch **never conflicts on upgrade**. |
| `15-prune-upstream-skills.patch` | **deletes upstream files** | Removes unused upstream bundled skills under `skills/` (apple, gaming, smart-home, red-teaming, github, the foreign-SaaS `productivity/*` integrations, etc.) so they aren't synced to profiles or listed in agent prompts. Generated with `git diff --binary` because pruned skills include binary assets (PDF templates). Can conflict on upgrade if upstream renames/removes a pruned skill. |
| `20-gateway-cloud-hooks.patch` | modifies upstream | `gateway/run.py` + `gateway/{stream_consumer,slash_commands}.py` + `gateway/platforms/{api_server,weixin}.py` + `plugins/platforms/{feishu,email,dingtalk,wecom}/adapter.py` — cloud integration hooks on the gateway/adapter layer (contextvars ACL propagation, peer-adapter push, origin headers, QR primitives, document text-extraction, turn-timing taps, email sender-role gate). **v0.18.0 (#41112) moved the feishu/email/dingtalk/wecom adapters out of `gateway/platforms/*.py` into the plugin layout `plugins/platforms/<p>/adapter.py`; our hooks re-homed there (api_server/weixin stayed put).** |
| `25-cron-cloud-hooks.patch` | modifies upstream | `cron/scheduler.py` — card-driven delivery: `deliver_render` jobs render their delivered message deterministically (see `_DELIVER_RENDERERS`) instead of using the agent's free-text reply. |
| `30-agent-cloud-hooks.patch` | modifies upstream | `agent/{prompt_builder,skill_utils,tool_executor,conversation_loop,system_prompt,auxiliary_client}.py`, `hermes_cli/{tools_config,config}.py`, `run_agent.py`, `tools/{clarify_tool,web_tools}.py`, `toolsets.py` — skills-cache fingerprint, skill-prompt extraction, surface-ACL enforcement + secret-file read guard, bundled-skills dir, registry-fallback `browser*` exclusion, per-message/per-tool latency taps. (v0.15.0's run_agent.py refactor moved the tool-dispatch loop into `agent/tool_executor.py`, so the surface-ACL hard-check lives there.) |
| `40-hermes-state-cloud-db.patch` | modifies upstream | `hermes_state.py` — dispatch `SessionDB` to the PG-backed implementation when `HERMES_USE_CLOUD_DB=1` (single-DB compliance). |
| `50-upstream-tests.patch` | modifies upstream | `tests/{agent/test_prompt_builder,conftest,tools/test_cronjob_tools}.py` — test adjustments for the above. |

Only patches 15/20/25/30/40/50 can conflict when upstream changes; `10`
is conflict-free by construction.

History: these consolidate 40 fine-grained patches (`01`..`54`) that
accumulated over many cloud epochs. The consolidation is byte-identical
— applying the 5 produces exactly the same hermes tree the 40 did.

## Mechanism

On `create_app()` startup, `hermes_cloud.app.hermes_patcher.ensure_hermes_patches_applied`
walks this directory in sorted order and for each `*.patch`:

1. `git apply --check --reverse` → already applied, skip.
2. Else `git apply --check` forward → clean state, apply it.
3. Else raise `PatchApplyError` — never silently run on a half-patched tree.

If the working tree is dirty or half-patched, the patcher hard-resets
the submodule to its gitlink target and reapplies from scratch.

## Regenerating patches (for maintainers)

After editing `hermes/` files:

```bash
./scripts/regen-hermes-patches.sh
```

This rebuilds all 7 patches from the current hermes working tree.
Commit the updated patch files plus any related hermes_cloud changes;
leave the hermes submodule pointer unchanged.

## Escape hatch

`HERMES_CLOUD_SKIP_PATCHES=1 python -m uvicorn ...` disables auto-apply.
Use only for debugging; normal operation always applies.

## Upgrading hermes

**Full step-by-step runbook: [UPGRADING.md](UPGRADING.md).**

In short: bump the `hermes` submodule pointer to the new upstream tag,
then reapply. `10-hermes-capabilities.patch` always applies cleanly (new
files only). For `15`/`20`/`25`/`30`/`40`/`50`, `git apply --check` reports
any conflict against the exact upstream file; resolve by editing the
hermes tree and re-running `regen-hermes-patches.sh`. Then run the test
suites and classify any failure (runner artifact / pre-existing bug /
real regression) before fixing — see the runbook.

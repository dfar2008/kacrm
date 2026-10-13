#!/usr/bin/env bash
# scripts/verify-patch-content.sh
#
# 在 commit 之前自检 hermes-patches/10-hermes-capabilities.patch 是否包含
# 已 shipped 的关键 anchor(skill 名 / 工具名)。
# 防止 regen 时 working tree 不全导致内容静默丢失。
#
# 用法:
#   ./scripts/verify-patch-content.sh
#   集成进 pre-commit hook 也可。
#
# 退出码: 0 = OK, 1 = anchor 缺失(规模回归)
#
# 语义说明:
#   - pattern 是 BRE 子串匹配(grep -c),不是精确等值
#   - count 是最低阈值(lower bound),实际出现次数 >= count 即 OK
#   - 本脚本捕获"低于底线"的回归,不验证"结构完整"
#   - 子串特性:pattern `crm-daily-coach` 也会匹配 `crm-daily-coach-v2`
#     若日后新增 — 这是有意为之,意味着计数会被抬高而非压低,
#     所以"漏 anchor"仍能稳定触发失败,不会被误掩盖。

set -euo pipefail

# Resolve repo root via the script's own location (NOT `git rev-parse
# --show-toplevel`, which would return the hermes/ submodule root if
# regen-hermes-patches.sh invoked us from inside hermes/).
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(dirname "$script_dir")"
PATCH="$repo_root/hermes-patches/10-hermes-capabilities.patch"

# Anchors:每行 "<count> <pattern> <description>"
# count = 该 pattern 应该至少出现 N 次
ANCHORS=(
  "3 crm-draft-style-distill draft-style skill 全套"
  "5 crm_draft_feedback_collect draft-style 采集工具"
  "1 crm-daily-coach daily coach skill"
  "1 crm_convert_lead lead 转化工具"
  "1 coach_proposal_submit coach 提交工具"
  # 新 sales methodology skills(本 plan Tasks 1-7 落地后补充)
  "3 crm-sales-discovery sales-methodology Discovery skill"
  "3 crm-sales-meddpicc sales-methodology MEDDPICC skill"
  "3 crm-sales-challenger sales-methodology Challenger skill"
  "3 crm-sales-pipeline sales-methodology Pipeline 三透镜 skill"
  "3 crm-sales-cadence sales-methodology Cadence skill"
  "3 crm-sales-account sales-methodology KA Account skill"
  "3 crm-sales-proposal sales-methodology Proposal 7 节 skill"
  "3 crm-sales-engineering sales-methodology Sales Engineering skill"
  # 8 个 sales skill 的"文件本身存在"校验 — 防止 crm-sales-discovery
  # 那种"references 还在但 SKILL.md diff 块消失"的隐形回归
  "1 skills/crm-sales-discovery/SKILL.md discovery SKILL.md diff 块"
  "1 skills/crm-sales-meddpicc/SKILL.md meddpicc SKILL.md diff 块"
  "1 skills/crm-sales-challenger/SKILL.md challenger SKILL.md diff 块"
  "1 skills/crm-sales-pipeline/SKILL.md pipeline SKILL.md diff 块"
  "1 skills/crm-sales-cadence/SKILL.md cadence SKILL.md diff 块"
  "1 skills/crm-sales-account/SKILL.md account SKILL.md diff 块"
  "1 skills/crm-sales-proposal/SKILL.md proposal SKILL.md diff 块"
  "1 skills/crm-sales-engineering/SKILL.md engineering SKILL.md diff 块"
  # AI 字段填值技能 — 2026-06-21 起 ai_fill dispatch 用它;曾因 regen 时
  # 工作树缺失而静默掉出 patch 10,加 anchor 守住(防 #1 回归)。
  "1 skills/crm-ai-attribute/SKILL.md crm-ai-attribute AI字段填值 skill"
)

fail=0
for entry in "${ANCHORS[@]}"; do
  read -r min_count pattern description <<< "$entry"
  actual=$(grep -c "$pattern" "$PATCH" || true)
  if [[ "$actual" -lt "$min_count" ]]; then
    echo "ANCHOR MISSING: '$pattern' found $actual times, expected >= $min_count ($description)"
    fail=1
  else
    echo "OK $pattern x $actual ($description)"
  fi
done

if [[ "$fail" -eq 1 ]]; then
  echo
  echo "Patch content regression detected. Likely cause: regen-hermes-patches.sh"
  echo "ran with incomplete submodule working tree."
  echo "Fix: ensure hermes_patcher applied current patch before regen, then re-run."
  exit 1
fi

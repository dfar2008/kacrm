#!/usr/bin/env bash
# scripts/push_skill_to_active_profiles.sh
#
# Force-resync 指定的 skill 从 bundled hermes/skills/ 到所有 per-profile 副本
# (~/.hermes/profiles/*/skills/<skill>/).
#
# 为什么需要:curator's sync_skills 不覆盖 per-profile 副本(故意 — 保留定制)。
# 这次方法论 + 蒸馏扩字段是必须全 profile 收到的内容更新,该脚本是一次性桥。
#
# 默认 dry-run。--apply 才真正执行。

set -euo pipefail

# 路径自动反推:脚本在 hermes-cloud/scripts/,bundle 在 ../hermes/skills/
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLED_SKILLS_DIR="${BUNDLED_SKILLS_DIR:-$SCRIPT_DIR/../hermes/skills}"
PROFILES_ROOT="${PROFILES_ROOT:-${HOME}/.hermes/profiles}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
MODE="dry-run"

DEFAULT_SKILLS=(
  crm-sales-discovery crm-sales-meddpicc crm-sales-challenger
  crm-sales-pipeline crm-sales-cadence crm-sales-account
  crm-sales-proposal crm-sales-engineering
  crm-daily-coach crm-draft-style-distill
)
SKILLS=("${DEFAULT_SKILLS[@]}")

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) MODE="apply" ; shift ;;
    --skill) SKILLS=("$2") ; shift 2 ;;
    --bundle) BUNDLED_SKILLS_DIR="$2" ; shift 2 ;;
    --profiles) PROFILES_ROOT="$2" ; shift 2 ;;
    -h|--help)
      cat <<HELP
Usage: $0 [--apply] [--skill <name>] [--bundle <dir>] [--profiles <dir>]

默认 dry-run,打印将要做什么但不动文件。
--apply 真正执行复制 + 备份旧文件(.bak.<timestamp>)。
--skill <name> 只同步一个 skill(不传时同步默认 9 个 sales/coach/distill)。
--bundle <dir> 覆盖 bundled skills 源目录(默认 \$SCRIPT_DIR/../hermes/skills)。
--profiles <dir> 覆盖 profiles 根目录(默认 \$HOME/.hermes/profiles)。

被覆盖的 profile 副本会先 mv 到 .bak.<timestamp>,可手动还原。
若 profile 的 SKILL.md frontmatter 的 name: 已被改名(定制化),
该 profile 的该 skill 会被 SKIP,留警告日志,不强覆盖。
HELP
      exit 0 ;;
    *) echo "Unknown arg: $1" >&2 ; exit 1 ;;
  esac
done

[[ -d "$BUNDLED_SKILLS_DIR" ]] || { echo "Bundle not found: $BUNDLED_SKILLS_DIR" >&2; exit 1; }
[[ -d "$PROFILES_ROOT" ]] || { echo "Profiles root not found: $PROFILES_ROOT" >&2; exit 1; }

echo "Mode: $MODE | Bundle: $BUNDLED_SKILLS_DIR | Profiles: $PROFILES_ROOT"
echo "Skills: ${SKILLS[*]}"
echo

resync_skill_for_profile() {
  local profile_id="$1" skill="$2"
  local src="$BUNDLED_SKILLS_DIR/$skill"
  local profile_skill_dir="$PROFILES_ROOT/$profile_id/skills/$skill"

  [[ -d "$src" ]] || { echo "  WARN: bundle missing for $skill, SKIP"; return; }

  if [[ -f "$profile_skill_dir/SKILL.md" ]]; then
    if ! grep -q "^name: $skill\$" "$profile_skill_dir/SKILL.md"; then
      echo "  WARN: $skill customized name in profile=$profile_id, SKIP"
      return
    fi
  fi

  if [[ "$MODE" == "dry-run" ]]; then
    echo "  would resync $skill -> profile=$profile_id"
  else
    mkdir -p "$(dirname "$profile_skill_dir")"
    [[ -e "$profile_skill_dir" ]] && mv "$profile_skill_dir" "$profile_skill_dir.bak.$TIMESTAMP"
    cp -R "$src" "$profile_skill_dir"
    echo "  resynced $skill -> profile=$profile_id"
  fi
}

while IFS= read -r -d '' profile_dir; do
  profile_id="$(basename "$profile_dir")"
  echo "-> profile=$profile_id"
  for s in "${SKILLS[@]}"; do
    resync_skill_for_profile "$profile_id" "$s"
  done
done < <(find "$PROFILES_ROOT" -mindepth 1 -maxdepth 1 -type d -print0)

echo
if [[ "$MODE" == "dry-run" ]]; then
  echo "DRY-RUN done. Re-run with --apply to actually copy."
else
  echo "RESYNC done. SIGTERM per-profile gateways to load new content."
fi

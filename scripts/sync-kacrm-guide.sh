#!/usr/bin/env bash
# Sync the KaCRM usage knowledge.
#
# 「使用指南」有两副面孔,内容同源:
#   1) hermes/skills/kacrm-guide/SKILL.md  —— agent 技能(operational,跨渠道答用法)
#   2) docs/brand/cloud-market/user-guide.html —— 给用户看的手册(渲染成 PDF)
# 改了产品功能时,两处都要更新。本脚本负责把【技能】这一面分发出去:
#   · 把新内容纳入 hermes-patches(规范留痕,纯增进 10-hermes-capabilities)
#   · 推送到本地所有 per-profile 副本
#   · 可选:rsync 到 prod + 推送 prod profiles + 重启 kacrm-cloud(只动技能,不重铺 hermes/)
#
# 用法(在【主仓】= hermes 子模块已检出 的检出里运行):
#   scripts/sync-kacrm-guide.sh            # 本地:留痕 patch + 推本地 profiles
#   scripts/sync-kacrm-guide.sh --prod     # 再 rsync 到 prod + 推 prod profiles + 重启
#
# 改完 user-guide.html 后,记得另用 Chrome/Playwright 重渲染 PDF(见 docs/brand/cloud-market/)。
set -euo pipefail

SKILL=kacrm-guide
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ ! -f "hermes/skills/$SKILL/SKILL.md" ]]; then
  echo "FATAL: hermes/skills/$SKILL/SKILL.md 不存在 —— 请在已检出 hermes 子模块的主仓里运行(git worktree 的 hermes/ 是空的)。" >&2
  exit 1
fi

echo "[sync] 1/2 regen-hermes-patches(把技能纳入 patch)"
./scripts/regen-hermes-patches.sh >/dev/null
grep -q "$SKILL" hermes-patches/10-hermes-capabilities.patch \
  && echo "      ✓ $SKILL 已在 10-hermes-capabilities.patch" \
  || { echo "FATAL: $SKILL 未进入 patch" >&2; exit 1; }

echo "[sync] 2/2 推送到本地所有 profile"
./scripts/push_skill_to_active_profiles.sh --skill "$SKILL" --apply | tail -4
echo "      提醒:本地 profile gateway 需 SIGTERM 重启才会加载新内容"

if [[ "${1:-}" == "--prod" ]]; then
  SSH_KEY="${SSH_KEY:-$HOME/.ssh/kacrm_deploy}"
  SSH_HOST="${SSH_HOST:-ubuntu@203.0.113.10}"
  REMOTE="${REMOTE:-/opt/kacrm}"
  PROFILES="${PROFILES:-/home/ubuntu/.kacrm/profiles}"
  SSH="ssh -i $SSH_KEY -o ConnectTimeout=25"
  echo "[sync] prod 1/3 rsync 技能 -> $SSH_HOST"
  rsync -az -e "ssh -i $SSH_KEY" "hermes/skills/$SKILL/" "$SSH_HOST:$REMOTE/hermes/skills/$SKILL/"
  echo "[sync] prod 2/3 推送到 prod profiles($PROFILES)"
  $SSH "$SSH_HOST" "cd '$REMOTE' && bash scripts/push_skill_to_active_profiles.sh --skill $SKILL --profiles '$PROFILES' --apply" | tail -4
  echo "[sync] prod 3/3 重启 kacrm-cloud"
  $SSH "$SSH_HOST" "sudo systemctl restart kacrm-cloud && sleep 16 && systemctl is-active kacrm-cloud && curl -s -o /dev/null -w 'health:%{http_code}\n' --max-time 6 http://localhost:8765/health"
fi

echo "[sync] done. 记得:user-guide.html 若同步改了,需重渲染 PDF。"

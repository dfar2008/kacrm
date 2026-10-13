#!/usr/bin/env bash
# Refresh per-profile skill copies from the bundled skills.
#
# Why: per-profile skill copies (<profiles_root>/*/skills/<name>/SKILL.md)
# SHADOW the bundled hermes/skills/ ones and never auto-update (the curator
# created them once; sync_skills won't overwrite). So edits to a bundled skill
# never reach a running profile's agent — it keeps reading its stale copy.
# (2026-06-21 incident: schema-evolution's ai_fill guidance was edited + shipped
# but the agent kept proposing workflows because it read the stale profile copy.)
#
# This refreshes ONLY skills that exist in BOTH bundled and the profile — i.e.
# system/framework skills with a stale shadow. Skills that exist ONLY in the
# profile (user/curator-authored) are left untouched.
#
# Usage: sync-profile-skills.sh <profiles_root> <bundled_skills_dir>
#   e.g. sync-profile-skills.sh /home/ubuntu/.kacrm/profiles \
#                               /opt/kacrm/hermes/skills
set -euo pipefail

ROOT="${1:?usage: sync-profile-skills.sh <profiles_root> <bundled_skills_dir>}"
BUNDLED="${2:?usage: sync-profile-skills.sh <profiles_root> <bundled_skills_dir>}"

[ -d "$ROOT" ]    || { echo "sync-skills: profiles_root not found: $ROOT (nothing to do)"; exit 0; }
[ -d "$BUNDLED" ] || { echo "sync-skills: bundled dir not found: $BUNDLED" >&2; exit 1; }

n=0
for pskills in "$ROOT"/*/skills; do
  [ -d "$pskills" ] || continue
  for sk in "$pskills"/*/; do
    [ -d "$sk" ] || continue
    name="$(basename "$sk")"
    if [ -f "$BUNDLED/$name/SKILL.md" ]; then
      cp -f "$BUNDLED/$name/SKILL.md" "$sk/SKILL.md" && n=$((n + 1))
      # keep auxiliary references/ in sync too, if the bundled skill has them
      if [ -d "$BUNDLED/$name/references" ]; then
        rm -rf "$sk/references"
        cp -rf "$BUNDLED/$name/references" "$sk/references"
      fi
    fi
  done
done
echo "sync-skills: refreshed $n per-profile skill file(s) from $BUNDLED"

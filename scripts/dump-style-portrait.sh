#!/usr/bin/env bash
# scripts/dump-style-portrait.sh
#
# Extract 「话术风格画像」+「方法论偏好画像」snapshots from a profile's
# USER.md for diff across time(baseline → day 8 → day 14)。

set -euo pipefail

PROFILES_ROOT="${PROFILES_ROOT:-${HOME}/.hermes/profiles}"
PROFILE_ID=""
OUTPUT_DIR="${OUTPUT_DIR:-./style-portrait-snapshots}"
LABEL="$(date +%Y%m%d-%H%M)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE_ID="$2" ; shift 2 ;;
    --out) OUTPUT_DIR="$2" ; shift 2 ;;
    --label) LABEL="$2" ; shift 2 ;;
    --profiles) PROFILES_ROOT="$2" ; shift 2 ;;
    -h|--help)
      cat <<HELP
Usage: $0 --profile <id> [--label <name>] [--out <dir>]

Extracts USER.md sections「话术风格画像」and「方法论偏好画像」for the given
profile, writing to <out>/<profile>-<label>-style.md and -methodology.md.

Run baseline-day(label baseline) / day-8 / day-14 to enable diff.
HELP
      exit 0 ;;
    *) echo "Unknown arg: $1" >&2 ; exit 1 ;;
  esac
done

[[ -n "$PROFILE_ID" ]] || { echo "--profile required" >&2; exit 1; }

USER_MD="$PROFILES_ROOT/$PROFILE_ID/USER.md"
[[ -f "$USER_MD" ]] || { echo "USER.md not found: $USER_MD" >&2; exit 1; }

mkdir -p "$OUTPUT_DIR"

style_out="$OUTPUT_DIR/$PROFILE_ID-$LABEL-style.md"
method_out="$OUTPUT_DIR/$PROFILE_ID-$LABEL-methodology.md"

# 话术风格画像 — extract section from header line down to next blank line
# followed by something that's not part of this section, or EOF.
# Simpler heuristic: grep -A 200 picks the section + bounded by the next
# top-level USER.md entry; trim manually after.

if grep -q "话术风格画像" "$USER_MD"; then
  sed -n '/话术风格画像/,/^更新于/p' "$USER_MD" > "$style_out"
  echo "Wrote style snapshot: $style_out ($(wc -l < "$style_out") lines)"
else
  echo "WARN: 「话术风格画像」section not found in $USER_MD"
  : > "$style_out"
fi

if grep -q "方法论偏好画像" "$USER_MD"; then
  sed -n '/方法论偏好画像/,/^更新于/p' "$USER_MD" > "$method_out"
  echo "Wrote methodology snapshot: $method_out ($(wc -l < "$method_out") lines)"
else
  echo "WARN: 「方法论偏好画像」section not found in $USER_MD (expected at baseline before first distill run)"
  : > "$method_out"
fi

echo
echo "To diff across snapshots:"
echo "  diff $OUTPUT_DIR/$PROFILE_ID-baseline-methodology.md \\"
echo "       $OUTPUT_DIR/$PROFILE_ID-day14-methodology.md"

#!/usr/bin/env bash
# scripts/verify-sales-coach-rollout.sh
#
# Run scripts/verify-coach-cards.sql against the configured DB and interpret
# results against the pass criteria from
# docs/runbooks/2026-05-31-sales-methodology-coach-verify.md.

set -euo pipefail

WINDOW="${WINDOW:-10 minutes}"
DB_HOST="${PGHOST:-localhost}"
DB_PORT="${PGPORT:-5432}"
DB_NAME="${PGDATABASE:-kacrm}"
DB_USER="${PGUSER:-postgres}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --window) WINDOW="$2" ; shift 2 ;;
    --db) DB_NAME="$2" ; shift 2 ;;
    --host) DB_HOST="$2" ; shift 2 ;;
    -h|--help)
      cat <<HELP
Usage: $0 [--window <interval>] [--db <name>] [--host <host>]

Defaults:
  --window "10 minutes"   (SQL INTERVAL syntax)
  --db kacrm
  --host localhost

Reads connection from PG* env vars; pass PGPASSWORD for auth.
HELP
      exit 0 ;;
    *) echo "Unknown arg: $1" >&2 ; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL="$SCRIPT_DIR/verify-coach-cards.sql"

[[ -f "$SQL" ]] || { echo "SQL kit not found: $SQL" >&2; exit 1; }

echo "Window: $WINDOW | DB: $DB_USER@$DB_HOST:$DB_PORT/$DB_NAME"
echo

psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
     -v WINDOW="'$WINDOW'" \
     -f "$SQL"

echo
echo "Manual checks remaining (per runbook §6):"
echo "  - Sample 5 cards above for human review:"
echo "    * reasoning names methodology skill + subframework + selection rationale?"
echo "    * draft uses skeleton hooks [...] not full Chinese sentences?"
echo "  - 0 cards is valid if data is sparse (no error)"

#!/usr/bin/env bash
# B0.1: supply-chain / ee-path check for integrations/mastra-studio.
# Exits nonzero on any hit. Artifacts -> $GATES (default sprint gates dir).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GATES="${GATES:-$ROOT/sprints/2026-10-13-mastra-studio-spike/gates}"
cd "$ROOT/integrations/mastra-studio"
mkdir -p "$GATES"
npm ci --ignore-scripts --no-audit --no-fund >/dev/null
rc=0
find node_modules -type d -name ee -path '*@mastra*' > "$GATES/ee-dirs.txt"
find node_modules/mastra -type d -name ee >> "$GATES/ee-dirs.txt" 2>/dev/null || true
grep -rEl "(@mastra/[a-z-]+/[^\"']*ee[/\"'])|(/ee/)" node_modules/@mastra node_modules/mastra \
  --include='*.js' --include='*.mjs' > "$GATES/ee-refs.txt" || true
node "$ROOT/scripts/mastra/license-census.mjs" > "$GATES/license-census.txt" || rc=1
[ -s "$GATES/ee-dirs.txt" ] && echo "ee dirs present (early warning; see loader trace)" >&2
exit $rc

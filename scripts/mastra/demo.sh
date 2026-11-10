#!/usr/bin/env bash
# Mastra Studio × ARAIL — repeatable demo. Manual, one-shot, local only.
#   scripts/mastra/demo.sh ["prompt"]     STOP=1 to stop Studio at the end.
# Steps: start Studio (loopback) -> run the Researcher once -> show the ledger
# probe entry -> show the listening socket -> point at the egress evidence.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PROMPT="${1:-List two facts about the moon}"
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "1/5  start Studio (idempotent; loopback only, offline flags set)"
./arailctl mastra start || { echo "start failed — run ./arailctl mastra install first"; exit 1; }

say "2/5  run the Researcher once: \"$PROMPT\""
before=$(ls -t lab/data/experiments 2>/dev/null | head -1)
./arailctl mastra run-once "$PROMPT" || echo "run-once returned non-zero (see output above)"
after=$(ls -t lab/data/experiments 2>/dev/null | head -1)

say "3/5  ledger entry (kind should be probe; not a 3DEval measurement)"
if [ -n "$after" ] && [ "$after" != "$before" ]; then
  python3 - "lab/data/experiments/$after" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(json.dumps({k: d.get(k) for k in ("id", "status", "hypothesis", "created_at")}, indent=2))
blob = json.dumps(d)
print("kind probe:", '"probe"' in blob, "| says not-a-3DEval-measurement:", "not a 3DEval measurement" in blob)
PY
else
  echo "no new experiment file appeared (see step 2 output)"
fi

say "4/5  who is listening on :4111 (expect 127.0.0.1 only)"
lsof -nP -iTCP:4111 -sTCP:LISTEN 2>/dev/null || echo "nothing listening"

say "5/5  egress evidence from the gate runs"
G=sprints/2026-10-13-mastra-studio-spike/gates
ls "$G"/D1-browser-attribution.md "$G"/D1-egress.json "$G"/D1-pcap-summary.txt 2>/dev/null
echo
echo "Open Studio:  ./arailctl mastra open   (then Traces -> agent_run -> researcher_run_once)"
echo "Portal page:  /mastra (maximus tier only; 404 on minimalist)"

[ "${STOP:-0}" = 1 ] && { say "stopping Studio"; ./arailctl mastra stop; }
exit 0

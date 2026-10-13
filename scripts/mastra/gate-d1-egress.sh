#!/usr/bin/env bash
# B0.2 D1 egress harness (no sudo). MODE=control runs WITHOUT MASTRA_TELEMETRY_DISABLED.
# Captures: lsof remote-address poll (250 ms) over the whole process tree + curl headers.
# Packet capture is NOT run here (needs root): see gates/D1-tcpdump-NOT-RUN.txt.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
G="${GATES:-$ROOT/sprints/2026-10-13-mastra-studio-spike/gates}"
MODE="${MODE:-test}"; SECS="${SECS:-60}"
cd "$ROOT/integrations/mastra-studio"
mkdir -p "$ROOT/lab/data/mastra"
ENVV=(env -i PATH="$PATH" HOME="$HOME" ARAIL_MASTRA_DB="$ROOT/lab/data/mastra/mastra.db")
[ "$MODE" = test ] && ENVV+=(MASTRA_TELEMETRY_DISABLED=1)
"${ENVV[@]}" npx --no-install mastra dev > "$G/D1-$MODE-server.log" 2>&1 &
ROOTPID=$!
: > "$G/D1-$MODE-lsof.txt"
end=$((SECONDS+SECS))
while [ $SECONDS -lt $end ]; do
  pids="$ROOTPID"; new="$ROOTPID"
  while [ -n "$new" ]; do new=$(for p in $new; do pgrep -P "$p"; done); pids="$pids $new"; done
  csv=$(echo $pids | tr ' ' ',')
  lsof -nP -i -a -p "$csv" 2>/dev/null | awk 'NR>1{print $1,$2,$8,$9,$10}' >> "$G/D1-$MODE-lsof.txt"
  if [ $((end-SECONDS)) -eq $((SECS-30)) ]; then
    for u in / /agents /api/agents; do curl -sI -m3 "http://127.0.0.1:4111$u" >> "$G/studio-headers-$MODE.txt"; echo >> "$G/studio-headers-$MODE.txt"; done
  fi
  sleep 0.25
done
pkill -P "$ROOTPID" 2>/dev/null; kill "$ROOTPID" 2>/dev/null
sort -u "$G/D1-$MODE-lsof.txt" > "$G/D1-$MODE-lsof.uniq.txt"
echo "done mode=$MODE"

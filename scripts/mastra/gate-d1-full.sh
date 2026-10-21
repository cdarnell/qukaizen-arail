#!/usr/bin/env bash
# NOTE: Python MCP child is not captured by name (venv python shows as Python); see D1-egress.json.
# D1 (no root): Studio already running on 127.0.0.1:4111. Poll sockets of node/python/browser processes
# while a headless browser views Studio traces and run-once.mjs executes. Env: ARAIL_DATA_DIR (scratch).
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"
G="${GATES:-$R/sprints/2026-10-13-mastra-studio-spike/gates}"
OUT="$G/D1-full-lsof.txt"; : > "$OUT"
( while [ ! -f "$G/.d1-stop" ]; do
    lsof -nP -i 2>/dev/null | awk 'NR>1 && $1 ~ /^(node|agent|ollama)/ {print $1,$2,$8,$9,$10}' >> "$OUT"
    sleep 0.25
  done ) &
POLL=$!
rm -f "$G/.d1-stop"
agent-browser open http://127.0.0.1:4111/ >/dev/null 2>&1
sleep 5
agent-browser open "http://127.0.0.1:4111/observability" >/dev/null 2>&1
"$R/scripts/mastra/run-once-env.sh" '{"goal":"Measure how responsive this lab is on local hardware"}' 2>/dev/null | tail -1 > "$G/D1-full-runonce.json"
sleep 5
agent-browser open "http://127.0.0.1:4111/observability" >/dev/null 2>&1
sleep 20
agent-browser network requests > "$G/D1-full-browser-requests.txt" 2>&1
agent-browser close >/dev/null 2>&1
touch "$G/.d1-stop"; wait $POLL; rm -f "$G/.d1-stop"
sort -u "$OUT" > "$G/D1-full-lsof-ours.txt"; rm -f "$OUT"
echo "remote (non-loopback) sockets:"
grep -- '->' "$G/D1-full-lsof-ours.txt" | grep -vE -- '->(127\.0\.0\.1|\[::1\]|localhost):' || echo "(none)"

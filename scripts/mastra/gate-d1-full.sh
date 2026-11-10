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
# Same restriction as `arailctl mastra open` (agent-browser cannot carry the host-resolver rule: it
# contains spaces/commas; that rule is exercised by the direct-Chrome netlog run, see D1-egress.json).
NL="$G/.netlog.json"; rm -f "$NL"
export AGENT_BROWSER_ARGS=$'--proxy-server=http://127.0.0.1:9\n--proxy-bypass-list=127.0.0.1;localhost;[::1]\n--disable-background-networking\n--log-net-log='"$NL"$'\n--net-log-capture-mode=Everything'
agent-browser close --all >/dev/null 2>&1
agent-browser open http://127.0.0.1:4111/ >/dev/null 2>&1
sleep 5
agent-browser open "http://127.0.0.1:4111/observability" >/dev/null 2>&1
"$R/arailctl" mastra run-once "Measure how responsive this lab is on local hardware" 2>/dev/null | tail -1 > "$G/D1-full-runonce.json"
sleep 5
agent-browser open "http://127.0.0.1:4111/observability" >/dev/null 2>&1
sleep 20
agent-browser network requests > "$G/D1-full-browser-requests.txt" 2>&1
agent-browser close >/dev/null 2>&1
sleep 3
python3 "$R/scripts/mastra/netlog-summary.py" "$NL" > "$G/D1-full-netlog-summary.json" 2>&1; rm -f "$NL"
touch "$G/.d1-stop"; wait $POLL; rm -f "$G/.d1-stop"
sort -u "$OUT" > "$G/D1-full-lsof-ours.txt"; rm -f "$OUT"
echo "remote (non-loopback) sockets:"
grep -- '->' "$G/D1-full-lsof-ours.txt" | grep -vE -- '->(127\.0\.0\.1|\[::1\]|localhost):' || echo "(none)"

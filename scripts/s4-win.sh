#!/usr/bin/env bash
# scripts/s4-win.sh - the S4 win test (sprint 2026-10-25-buddy-s4-gateway-in-box, VISION "Win condition").
#
# Starts a throwaway portal (its own LAB_ROOT, DATA_DIR, .env, ports; nothing of your real lab is
# touched), then checks criteria 1-5 and prints every number with what it means, why the threshold
# is what it is, and where a failure points. Criterion 6 (the phone) is the operator's.
#
#   scripts/s4-win.sh              # needs the repo .venv (or PYTHON=...)
#   PW_PYTHON=/path/to/python-with-playwright scripts/s4-win.sh   # adds the 13-surface browser run
#
# Exit 0 only if every check passes.
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$REPO_ROOT"
PY="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/s4win.XXXXXX")"
trap '[[ -n "${PORTAL_PID:-}" ]] && kill "$PORTAL_PID" 2>/dev/null; wait "$PORTAL_PID" 2>/dev/null; rm -rf "$TMP"' EXIT

free_port() { "$PY" -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])'; }
export PORTAL_PORT="$(free_port)" ARAIL_EDGE_PORT="$(free_port)"
export LAB_ROOT="$TMP/lab" ARAIL_DATA_DIR="$TMP/lab/data" ARAIL_ENV_FILE="$TMP/lab/.env" HOME="$TMP/home"
export BIND_ADDR=127.0.0.1 LAB_TIER=maximus ARAIL_BOOT_WARM=0 PYTHONPATH="$REPO_ROOT/src"
unset ARAIL_PASSWORD ARAIL_INSTANCE
mkdir -p "$LAB_ROOT" "$HOME"
chmod 700 "$LAB_ROOT"
: > "$ARAIL_ENV_FILE"
PASS="s4-win-$(date +%s)-passphrase"

(cd "$LAB_ROOT" && exec "$PY" -m uvicorn arail.portal.app:app --host 127.0.0.1 --port "$PORTAL_PORT" \
    --app-dir "$REPO_ROOT/src" --log-level warning >"$TMP/portal.log" 2>&1) &
PORTAL_PID=$!
for _ in $(seq 1 120); do
    curl -sf "http://127.0.0.1:$PORTAL_PORT/api/instance" >/dev/null 2>&1 && break
    sleep 0.5
done
curl -sf "http://127.0.0.1:$PORTAL_PORT/api/instance" >/dev/null || { echo "portal did not start; log:"; tail -20 "$TMP/portal.log"; exit 2; }

export S4_TMP="$TMP" S4_PASS="$PASS" S4_PORTAL_PID="$PORTAL_PID" S4_REPO="$REPO_ROOT"
"$PY" "$REPO_ROOT/scripts/s4_win_checks.py"
RC=$?

if [[ -n "${PW_PYTHON:-}" ]]; then
    echo
    echo "== criterion 2: the dashboard still works (real browser, 13 surfaces of the maximus tier) =="
    SURF="$("$PY" -c 'from arail.portal.app import _TIER_SURFACES as T
P={"dashboard":"/"}
print(",".join(k+":"+P.get(k,"/"+k) for k in ["dashboard","chat","research","dac","agents","admin","docs","notebooks","terminal","tuning","plugins","study","forge"] if k in T["maximus"]))')"
    "$PW_PYTHON" "$REPO_ROOT/scripts/s4_surfaces.py" --base "http://127.0.0.1:$PORTAL_PORT" --passphrase "$PASS" \
        --surfaces "$SURF" --extra worlds:/worlds,graph:/graph,mission:/mission,skills:/skills || RC=1
else
    echo
    echo "criterion 2 (browser run): skipped - set PW_PYTHON to a python with playwright to run it"
fi
echo
[[ $RC -eq 0 ]] && echo "S4 WIN TEST: PASS" || echo "S4 WIN TEST: FAIL (see the lines marked FAIL above)"
exit $RC

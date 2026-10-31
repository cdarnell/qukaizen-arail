#!/usr/bin/env bash
# deep-honest-win.sh — the one live check of sprint 2026-10-29-arail-deep-honest.
#
# Runs on a Mac that has the real QueueLLM wheel and the real model. On today's broken wheel
# (it answers call 1 and fails every later call in a process) it shows that ARAIL says so on
# every surface and never says deep is fine or the work done. It never changes the operator's
# venv, .env, Worlds or QueueLLM checkout. Token-free: local only, nothing is downloaded.
# Not part of pytest. About 3 to 5 minutes.
#
#   ARAIL_WIN_PYTHON=~/ProJects/qukaizen-arail/.venv/bin/python ./scripts/deep-honest-win.sh
#
# Inputs (environment)
#   ARAIL_WIN_PYTHON     the interpreter whose aerollm_api is tested; used read only.
#                        Default: $REPO_ROOT/.venv/bin/python
#   ARAIL_WIN_ENV_FROM   the .env to read four keys from (AEROLLM_MODEL, ARAIL_MODELS_DIR,
#                        AEROLLM_KV_BUDGET_PCT, LAB_TIER). Nothing else is copied.
#                        Default: $REPO_ROOT/.env
#   ARAIL_WIN_QUEUELLM   the QueueLLM checkout, read only (git state before and after, and the
#                        sibling the build script is shown refusing).
#                        Default: $ARAIL_AEROLLM_REPO, else $HOME/ProJects/qukaizen-queuellm
#   ARAIL_WIN_JSON_OUT   also write the whole result as JSON to this file
#   ARAIL_WIN_KEEP=1     keep the scratch root (row logs are in it) instead of removing it
#   ARAIL_WIN_ROW_TIMEOUT_S  seconds a row may take (default 900)
# Extra arguments go to scripts/deep_honest_win.py, for example: --only W5,W6
#
# Isolation: one mktemp -d root. Each row gets its own data, lab, pkb, worlds and verdict
# folders, its own .env and no model-defaults file; PYTHONDONTWRITEBYTECODE=1, an offline
# Hugging Face, airgapped mode, a random portal token and password, and no port bound.
#
# Exit: 0 only when every row passes. 1 if a row failed or could not test its claim
# (INCONCLUSIVE is not a pass). 2 if the inputs are wrong. 130 if interrupted.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WIN_PYTHON="${ARAIL_WIN_PYTHON:-$REPO_ROOT/.venv/bin/python}"
ENV_FROM="${ARAIL_WIN_ENV_FROM:-$REPO_ROOT/.env}"
QUEUELLM="${ARAIL_WIN_QUEUELLM:-${ARAIL_AEROLLM_REPO:-$HOME/ProJects/qukaizen-queuellm}}"
SAFE_PATH="/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"

fail() { printf 'deep-honest-win: %s\n' "$*" >&2; exit 2; }

[[ -x "$WIN_PYTHON" ]] || fail "ARAIL_WIN_PYTHON is not an executable file: $WIN_PYTHON (set ARAIL_WIN_PYTHON to the venv python whose aerollm_api you want tested)"
[[ -r "$ENV_FROM" ]] || fail "no .env to read the four model keys from: $ENV_FROM (set ARAIL_WIN_ENV_FROM)"
[[ -f "$REPO_ROOT/scripts/deep_honest_win.py" ]] || fail "scripts/deep_honest_win.py is missing next to this script"

ROOT="$(mktemp -d "${TMPDIR:-/tmp}/arail-deep-honest.XXXXXX")"
chmod 700 "$ROOT"
# shellcheck disable=SC2329  # runs from the EXIT trap below
cleanup() {
    local rc=$?
    if [[ "${ARAIL_WIN_KEEP:-0}" == "1" ]]; then
        printf 'scratch root kept: %s\n' "$ROOT" >&2
    elif [[ -n "$ROOT" && -d "$ROOT" && "$(basename "$ROOT")" == arail-deep-honest.* ]]; then
        rm -rf -- "$ROOT"
    fi
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

mkdir -p "$ROOT/home" "$ROOT/tmp"
# Four non-secret keys, and nothing else: the real .env also holds the lab password and provider keys.
SCRATCH_ENV="$ROOT/env.scratch"
: > "$SCRATCH_ENV"
chmod 600 "$SCRATCH_ENV"
grep -E '^(AEROLLM_MODEL|ARAIL_MODELS_DIR|AEROLLM_KV_BUDGET_PCT|LAB_TIER)=' "$ENV_FROM" >> "$SCRATCH_ENV" || true

rc=0
env -i PATH="$SAFE_PATH" HOME="$ROOT/home" TMPDIR="$ROOT/tmp" LANG=en_US.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 \
    ARAIL_WIN_ROW_TIMEOUT_S="${ARAIL_WIN_ROW_TIMEOUT_S:-900}" \
    "$WIN_PYTHON" "$REPO_ROOT/scripts/deep_honest_win.py" \
    --root "$ROOT" --repo "$REPO_ROOT" --python "$WIN_PYTHON" --env-from "$SCRATCH_ENV" \
    --queuellm "$QUEUELLM" ${ARAIL_WIN_JSON_OUT:+--json-out "$ARAIL_WIN_JSON_OUT"} "$@" || rc=$?
exit "$rc"

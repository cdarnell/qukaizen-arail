#!/usr/bin/env bash
# Run run-once.mjs with the gate child env (mirrors _mastra_env()). Usage: run-once-env.sh '<json>'
# Extra env (e.g. NODE_OPTIONS, TRACE_LOADS_OUT) may be set by the caller via EXTRA_ENV="K=V K=V".
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"
SD="${ARAIL_DATA_DIR:?set ARAIL_DATA_DIR to a scratch lab data dir}"
cd "$R/integrations/mastra-studio"
exec env -i PATH="$PATH" HOME="$HOME" MASTRA_TELEMETRY_DISABLED=1 MASTRA_OFFLINE=1 \
  MASTRA_AUTO_REFRESH_PROVIDERS=false ARAIL_DATA_DIR="$SD" ARAIL_MASTRA_DB="$SD/mastra/mastra.db" \
  ARAIL_PYTHON="$R/.venv/bin/python" LAB_TIER=maximus LAB_MODE=airgapped PYTHONPATH="$R/src" ARAIL_ROOT="$R" LAB_ROOT="${LAB_ROOT:-$SD/lab}" LAB_PKB="${LAB_PKB:-$SD/lab/pkb}" \
  ${EXTRA_ENV:-} node run-once.mjs "$1"

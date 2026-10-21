#!/usr/bin/env bash
# ./arailctl status — show what's running and where.
#
# ARCHITECTURE.md §7/§8/§12 (sprints/2026-07-29-elite-cli, WP5, Ruling 2):
# ONE collector, ONE in-memory document (schema arail.status/v2), TWO
# renderers (the human table and --json) — so they can never disagree.
# HTTP probes (scripts/lib/services.sh), not `pgrep` patterns, are the
# verdict source; `pgrep` survives only as an "owner" hint, run only when
# a port is already known to be listening. Exit codes are additive: the
# historic `0` stays "ok", new `3` (degraded) and `4` (nothing running)
# join it (§12.1) — `status` never exited non-zero before this WP.
#
# REVIEW.md m4 — LANDMINE, read before touching error handling in this
# file: `set -uo pipefail`, deliberately WITHOUT `-e`. Every other
# `scripts/*.sh` in this repo runs under `set -euo pipefail`; this one
# does not, and it is not an oversight from the WP5 rewrite that produced
# this file's current shape. The probe helpers this file calls
# (scripts/lib/services.sh's svc_listening/svc_http_status/etc., and this
# file's own instance-record readers) use a NON-zero return as DATA —
# "down"/"unreadable"/"unknown" are legitimate outcomes on the way to
# building the status document, not failures that should abort the
# collector. Running the whole file under `-e` would turn every one of
# those expected-degraded states into an immediate, undiagnosed exit,
# which is exactly the "while read ... aborts a $( ) assignment" class of
# bug F20 already names. The NEXT maintainer should not assume errexit is
# still on here just because it is everywhere else in this repo — grep for
# `set -e` failing silently on a probe helper's nonzero return before
# reaching for it. (A narrower, `set +e`/`set -e` pair scoped tightly
# around just the probe block was considered and NOT done in this
# same-sprint review-fix pass — it would touch every probe call site in a
# 782-line file with no numbered test currently pinning the scoped
# boundary, which is a larger, riskier change than this fix's own scope.)
set -uo pipefail

# `pwd -P` (physical, symlinks resolved) — NOT plain `pwd` (logical). Must
# match Python's Path.cwd()/os.getcwd(), which the OS always returns
# physical, and start.sh's own REPO_ROOT resolution (REVIEW.md m5,
# concurrent-worlds sprint). Without this, the new root-identity check
# (portal's own reported "checkout" == REPO_ROOT) reports "foreign" on any
# checkout reached through a symlinked directory component (F2) — a latent
# bug this file carried since before identity-checking existed here.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$REPO_ROOT"

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/lib/instances.sh"
# Root-lab per-service probing (svc_listening/svc_http_status/...) — see
# arailctl's identical guard for the source-discipline rationale (A2/F4):
# this file has never been unit-tested as a standalone copy the way
# reset.sh has, but the guard costs nothing and keeps the discipline
# uniform across every script that can reach a fresh checkout.
# shellcheck disable=SC1091
[[ -f "$REPO_ROOT/scripts/lib/services.sh" ]] && source "$REPO_ROOT/scripts/lib/services.sh"

# ── ANSI color gating (ARCHITECTURE.md §13 "ANSI leaks into non-tty
# output", F25) — see arailctl's identical block for the full rationale.
# $'...' (ANSI-C quoting) so the variables hold real ESC bytes.
if [[ -t 1 && "${ARAIL_COLOR:-auto}" != "never" && -z "${NO_COLOR:-}" ]] || [[ "${ARAIL_COLOR:-auto}" == "always" ]]; then
    BOLD=$'\033[1m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
    BOLD=""; GREEN=""; YELLOW=""; DIM=""; RESET=""
fi
ok()   { echo -e "  ${GREEN}✓${RESET} $*"; }
warn() { echo -e "  ${YELLOW}⚠${RESET} $*"; }
dim()  { echo -e "  ${DIM}$*${RESET}"; }

# shellcheck disable=SC1091
[[ -f .env ]] && set -a && source .env && set +a
# `source <missing-file> || true` does NOT reach the `|| true` under bash
# 3.2 (macOS's shipped /bin/bash) — a "file not found" source error aborts
# a non-interactive shell outright even under a trailing `||`. Guard with
# an existence check instead (ARCHITECTURE.md §10's "Ruling on the two
# latent fixes" for start.sh, applied identically here).
# shellcheck disable=SC1091
[[ -f lab.conf ]] && set -a && source lab.conf && set +a
BIND="${BIND_ADDR:-127.0.0.1}"
LAB_NAME="${LAB_NAME:-Arail}"
LAB_TIER_EFF="${LAB_TIER:-minimalist}"
LAB_MODE_EFF="${LAB_MODE:-${ARAIL_MODE:-airgapped}}"
LANCE_PORT="${LANCE_PORT:-7414}"

# F3 (ARCHITECTURE.md §15, sprints/2026-07-29-elite-cli): a half-written
# lab.conf (interrupted `setup`) can leave a non-numeric port value, e.g.
# PORTAL_PORT=not-a-number. Left unguarded, that reaches _status_emit_
# service_json's `int()` as a raw ValueError traceback AND silently drops
# the affected row out of root.services[] (the caller never gets that far).
# Validate every port variable ONCE, here, before anything downstream reads
# one: an invalid value warns once, falls back to the documented default,
# and the row is built normally — never abort, never drop a row (§7.2/7.3).
STATUS_WARNINGS=()
_status_valid_port() {
    [[ "$1" =~ ^[0-9]+$ ]] && (( 10#$1 >= 1 && 10#$1 <= 65535 ))
}
_status_sanitize_port() {
    local varname="$1" default="$2" current
    # NOT `local varname=... current="${!varname:-}"` on one line: bash
    # expands every word of a compound `local` statement BEFORE any of
    # that statement's assignments take effect, so `${!varname}` there
    # sees the OUTER scope's `varname` (unset), not the one just assigned
    # — silently reading empty every time. Assign, THEN indirect-expand.
    current="${!varname:-}"
    if [[ -z "$current" ]]; then
        printf -v "$varname" '%s' "$default"
        return
    fi
    if ! _status_valid_port "$current"; then
        STATUS_WARNINGS+=("${varname}='${current}' is not a valid port (1-65535) — falling back to the default ${default}")
        printf -v "$varname" '%s' "$default"
    fi
}
_status_sanitize_port PORTAL_PORT 8080
_status_sanitize_port LANCE_PORT 7414
_status_sanitize_port MLX_OPENAI_PORT 11435
_status_sanitize_port TERMINAL_PORT 7681
_status_sanitize_port NOTEBOOK_PORT 8888
_status_sanitize_port IDE_PORT 8443

_status_usage() {
    echo "Usage: ./arailctl status [--json[=full|instances]] [--probe|--no-probe] [--quiet|-q] [--no-sizes]"
}

# ── Flags (ARCHITECTURE.md §5.2/§7.3) ───────────────────────────────────
# --json / --json=full  -> the whole arail.status/v2 document
# --json=instances       -> the bare v1 rows array (byte-compatible with
#                            today's --json output — the documented stable
#                            form, docs/concurrent-worlds.md)
# --json=<anything-else> -> exit 2
STATUS_JSON_MODE=""     # "" | "full" | "instances"
STATUS_PROBE=0          # --probe (also read by _status_build_rows, unchanged)
STATUS_NO_PROBE=0       # --no-probe
STATUS_QUIET=0          # --quiet | -q
STATUS_NO_SIZES=0       # --no-sizes
for _status_arg in "$@"; do
    case "$_status_arg" in
        --json)          STATUS_JSON_MODE="full" ;;
        --json=full)      STATUS_JSON_MODE="full" ;;
        --json=instances) STATUS_JSON_MODE="instances" ;;
        --json=*)
            echo "bad --json value: '${_status_arg#--json=}' (use --json, --json=full, or --json=instances)" >&2
            exit 2 ;;
        --probe)     STATUS_PROBE=1 ;;
        --no-probe)  STATUS_NO_PROBE=1 ;;
        --quiet|-q)  STATUS_QUIET=1 ;;
        --no-sizes)  STATUS_NO_SIZES=1 ;;
        -h|--help)   _status_usage; exit 0 ;;
        *)
            echo "Unknown flag: $_status_arg" >&2
            _status_usage >&2
            exit 2 ;;
    esac
done
unset _status_arg

# --no-probe wins if both are given (the safer, more deterministic bias —
# CI mode never silently upgrades itself back to a network probe).
if [[ "$STATUS_NO_PROBE" == "1" ]]; then
    PROBE_LEVEL="none"
elif [[ "$STATUS_PROBE" == "1" ]]; then
    PROBE_LEVEL="extended"
else
    PROBE_LEVEL="default"
fi

HAVE_CURL=0
command -v curl >/dev/null 2>&1 && HAVE_CURL=1
HAVE_LISTEN_TOOL=0
{ command -v lsof >/dev/null 2>&1 || command -v ss >/dev/null 2>&1; } && HAVE_LISTEN_TOOL=1

# svc_listening (scripts/lib/services.sh) requires the real _port_in_use
# already loaded into THIS shell (inst_load_port_helpers, instances.sh) —
# it deliberately never re-implements port detection itself (§8.1's
# boundary). Without this, every svc_listening call silently reports
# "undetectable" (return 2) regardless of whether lsof/ss are actually
# present, which would misreport every service as `state: "unknown"`
# instead of an honest up/down. A missing scripts/setup.sh (a copied-out
# test fixture with only status.sh) degrades the same way as no lsof/ss —
# svc_listening's own contract already treats that as undetectable, never
# as "down" (A4).
declare -F svc_listening >/dev/null 2>&1 && inst_load_port_helpers >/dev/null 2>&1

[[ "$HAVE_CURL" == "0" ]] && STATUS_WARNINGS+=("curl not found — HTTP probes skipped")
[[ "$HAVE_LISTEN_TOOL" == "0" ]] && STATUS_WARNINGS+=("lsof/ss not found — listen probes skipped (state: unknown)")

# ── Generic bash-array -> JSON-array reducer (TAB/newline discipline, A2's
# "collections cross function boundaries as stdout, never as a second data
# structure"). Every caller filters blank lines here rather than upstream,
# so an EMPTY bash array (a real, common case: 0 warnings, 0 root services
# before the portal is even probed) never needs the bash-3.2 "empty array
# under set -u" workaround (F19/F20-adjacent — discovered the hard way
# during WP4's restart_driver.sh work; documented in BUILD_LOG.md).
_status_json_lines_to_array() {
    python3 -c '
import json, sys
rows = []
for line in sys.stdin:
    line = line.strip()
    if line:
        rows.append(json.loads(line))
print(json.dumps(rows))
'
}

# ── Instances (ARCHITECTURE.md §4.1, unchanged from the pre-WP5 file) ───
# Registry-driven, no-network by default (predicate steps 1-3 only). This
# is the ONE part of the collector this WP does not touch: instances[]
# rows must stay byte-identical to today's (T3, T12 — `jq .instances`
# reproduces `--json=instances` exactly).
_status_build_rows() {
    local slug rec rc probe_field data_dir_field
    while IFS= read -r slug; do
        [[ -n "$slug" ]] || continue
        if rec="$(inst_read_record "$slug" 2>/dev/null)"; then
            rc=0
        else
            rc=$?
        fi
        if (( rc == 2 )); then
            printf '{"slug":%s,"state":"unreadable"}\n' "$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$slug")"
            continue
        fi
        (( rc == 0 )) || continue

        data_dir_field="$(inst_record_field "$rec" data_dir)"
        local data_missing="false"
        [[ -n "$data_dir_field" && -d "$data_dir_field" ]] || data_missing="true"

        if inst_alive "$slug"; then
            probe_field="null"
            if [[ "$STATUS_PROBE" == "1" ]]; then
                if inst_probe_matches "$slug" "$rec"; then
                    probe_field="null"
                else
                    local bind port raw
                    bind="$(inst_record_field "$rec" bind)"
                    port="$(inst_record_field "$rec" portal_port)"
                    raw="$(curl -sf -m 0.7 "http://${bind}:${port}/api/instance" 2>/dev/null || true)"
                    probe_field="$(python3 -c '
import json, sys
raw = sys.argv[1]
try:
    d = json.loads(raw)
    print(json.dumps(d.get("checkout", "")))
except Exception:
    print("null")
' "$raw")"
                fi
            fi
            python3 -c '
import json, sys
rec = json.loads(sys.argv[1])
rec["state"] = "live"
rec["data_root_missing"] = (sys.argv[2] == "true")
probe = sys.argv[3]
rec["probe_mismatch_checkout"] = json.loads(probe) if probe != "null" else None
print(json.dumps(rec))
' "$rec" "$data_missing" "$probe_field"
        else
            python3 -c '
import json, sys
rec = json.loads(sys.argv[1])
rec["state"] = "stale"
print(json.dumps(rec))
' "$rec"
        fi
    done < <(inst_list_slugs)
    # A `while read` loop's own exit status is that of its final `read`
    # (nonzero at EOF) when the body ran zero or more times — under
    # `set -eo pipefail` this would abort the caller's assignment the
    # moment a repo has zero registered instances (F20's exact class).
    # This function must always return 0.
    return 0
}

# F17/T8: distinguish "0 registered instances" (fine) from "registry.d
# exists but this process cannot even read it" (a real operational fault —
# a chmod'd-wrong directory, an interrupted setup) — `inst_list_slugs`
# degrades the LATTER into the FORMER silently (a bare glob against an
# unreadable dir just finds nothing), so it must be detected separately,
# before the instances collector ever runs.
REGISTRY_UNREADABLE=0
_status_reg_dir="$(inst_registry_dir)"
if [[ -d "$_status_reg_dir" && ! -r "$_status_reg_dir" ]]; then
    REGISTRY_UNREADABLE=1
    STATUS_WARNINGS+=("registry directory is unreadable: ${_status_reg_dir}")
fi

# REVIEW.md n1: this used to re-inline a second, byte-identical copy of
# _status_json_lines_to_array's own python reducer instead of calling it —
# the ONE thing every other JSON-lines-to-array site in this file
# (AGENTS_JSON, ROOT_SERVICES_JSON, below) already does.
INSTANCES_JSON="$(_status_build_rows | _status_json_lines_to_array)"

ANY_LIVE_COUNT=0
ANY_LIVE_SLUG=""
while IFS= read -r _status_s; do
    [[ -n "$_status_s" ]] || continue
    if inst_alive "$_status_s"; then
        ANY_LIVE_COUNT=$((ANY_LIVE_COUNT + 1))
        [[ -z "$ANY_LIVE_SLUG" ]] && ANY_LIVE_SLUG="$_status_s"
    fi
done < <(inst_list_slugs)
unset _status_s

# ── Supervision (daemon_active is THE liveness predicate — instances.sh
# owns it; this file never re-derives it, ARCHITECTURE.md §14.2) ────────
DAEMON_ACTIVE=0
daemon_active && DAEMON_ACTIVE=1
PLISTS_INSTALLED=0
daemon_plist_installed && PLISTS_INSTALLED=1

_STATUS_AGENT_LINES=()
_status_collect_agent() {
    local label="$1"
    [[ -f "$HOME/Library/LaunchAgents/${label}.plist" ]] || return 0
    local raw line
    raw="$(launchctl list "$label" 2>/dev/null || true)"
    line="$(python3 -c '
import json, re, sys
raw = sys.stdin.read()
label = sys.argv[1]
pid_m = re.search(r"\"PID\"\s*=\s*(-?\d+)\s*;", raw)
exit_m = re.search(r"\"LastExitStatus\"\s*=\s*(-?\d+)\s*;", raw)
pid = int(pid_m.group(1)) if pid_m else None
last_exit = int(exit_m.group(1)) if exit_m else None
state = "running" if pid is not None else ("installed" if raw.strip() else "not-loaded")
print(json.dumps({"label": label, "state": state, "pid": pid, "last_exit_status": last_exit}))
' "$label" <<< "$raw")"
    _STATUS_AGENT_LINES+=("$line")
    return 0
}
if [[ "$PLISTS_INSTALLED" == "1" ]]; then
    _status_collect_agent io.arail.portal
    _status_collect_agent io.arail.memory
    _status_collect_agent io.arail.mlx
fi
AGENTS_JSON="$(printf '%s\n' "${_STATUS_AGENT_LINES[@]:-}" | _status_json_lines_to_array)"

# ── Root-lab per-service probing (ARCHITECTURE.md §7, §8.1) ─────────────
DISPLAY_HOST="$BIND"
PROBE_HOST="127.0.0.1"
declare -F svc_probe_host >/dev/null 2>&1 && PROBE_HOST="$(svc_probe_host "$BIND")"
PORTAL_PORT_EFF="${PORTAL_PORT:-8080}"

_status_emit_service_json() {
    # name port expected(true/false) listening(true/false/"") http_status
    # owner url state detail
    python3 -c '
import json, sys
name, port, expected, listening, http_status, owner, url, state, detail = sys.argv[1:10]
def b(x):
    return None if x == "" else x == "true"
def i(x):
    return None if x == "" else int(x)
def s(x):
    return None if x == "" else x
print(json.dumps({
    "name": name, "expected": expected == "true", "port": i(port),
    "url": s(url), "listening": b(listening), "http_status": s(http_status),
    "owner": owner if owner else "unknown", "state": state, "detail": s(detail),
}))
' "$@"
}

PORTAL_LISTENING=""
PORTAL_HTTP_STATUS=""
PORTAL_IDENTITY_OK=""
PORTAL_FOREIGN_CHECKOUT=""
if declare -F svc_listening >/dev/null 2>&1; then
    svc_listening "$PORTAL_PORT_EFF"; _status_rc=$?
    case "$_status_rc" in
        0) PORTAL_LISTENING="true" ;;
        1) PORTAL_LISTENING="false" ;;
        *) PORTAL_LISTENING="" ;;
    esac
fi
# Portal HTTP is probed even at the DEFAULT level (not gated by --probe) —
# it is the one probe that resolves the "Portal not running" vs. "World
# instance is happily serving" contradiction (§7.2). Only --no-probe skips
# it entirely (deterministic CI mode).
if [[ "$PROBE_LEVEL" != "none" && "$HAVE_CURL" == "1" ]]; then
    _status_portal_raw="$(svc_http_status "http://${PROBE_HOST}:${PORTAL_PORT_EFF}/api/instance" 0.7)"
    PORTAL_HTTP_STATUS="${_status_portal_raw%%$'\t'*}"
    _status_portal_body="${_status_portal_raw#*$'\t'}"
    [[ "$PORTAL_HTTP_STATUS" == "000" ]] && PORTAL_HTTP_STATUS=""
    if [[ -n "$PORTAL_HTTP_STATUS" ]]; then
        _status_slug="$(_svc_json_field "$_status_portal_body" slug)"
        _status_checkout="$(_svc_json_field "$_status_portal_body" checkout)"
        if [[ "$_status_slug" == "root" && -n "$_status_checkout" && "$_status_checkout" == "$REPO_ROOT" ]]; then
            PORTAL_IDENTITY_OK="true"
        else
            PORTAL_IDENTITY_OK="false"
            PORTAL_FOREIGN_CHECKOUT="$_status_checkout"
        fi
    fi
fi

# root.state — first match wins (ARCHITECTURE.md §7.1's priority table).
ROOT_STATE=""
ROOT_REASON=""
if [[ "$PORTAL_IDENTITY_OK" == "false" ]]; then
    ROOT_STATE="foreign"
    ROOT_REASON="a DIFFERENT checkout answers :${PORTAL_PORT_EFF}: ${PORTAL_FOREIGN_CHECKOUT:-<unreadable>}"
elif [[ "$PORTAL_IDENTITY_OK" == "true" ]]; then
    ROOT_STATE="up"   # may be downgraded to "degraded" once the other services are probed
elif [[ "$PORTAL_LISTENING" == "true" ]]; then
    # Listening but identity unverifiable — either --no-probe (zero HTTP by
    # design) or a non-conforming response. Best-effort "up", never a false
    # "down"; A4/F30: a missing capability never degrades the verdict.
    ROOT_STATE="up"
    [[ "$PROBE_LEVEL" == "none" ]] && STATUS_WARNINGS+=("root portal identity not verified (--no-probe)")
elif [[ "$DAEMON_ACTIVE" == "1" ]]; then
    ROOT_STATE="down"
    ROOT_REASON="launchd supervises it but the portal is not answering"
elif (( ANY_LIVE_COUNT > 0 )); then
    ROOT_STATE="not-started"
    ROOT_REASON="a World instance is running ('${ANY_LIVE_SLUG}'); the root lab was never started"
else
    ROOT_STATE="down"
    ROOT_REASON="not running — ./arailctl start"
fi

_STATUS_ROOT_SERVICE_LINES=()
_status_emit_portal_line() {
    local expected="true" state="unknown" detail="" url="" owner="unknown"
    if [[ "$ROOT_STATE" == "not-started" ]]; then
        _STATUS_ROOT_SERVICE_LINES+=("$(_status_emit_service_json portal "$PORTAL_PORT_EFF" false "" "" "unknown" "" skipped "root lab not started")")
        return
    fi
    if [[ "$PORTAL_LISTENING" == "true" ]]; then
        url="http://${DISPLAY_HOST}:${PORTAL_PORT_EFF}"
        pgrep -f "uvicorn.*arail\.portal\.app.*--port ${PORTAL_PORT_EFF}" >/dev/null 2>&1 && owner="ours"
    fi
    case "$ROOT_STATE" in
        foreign) state="foreign"; detail="$ROOT_REASON" ;;
        up|degraded) state="up" ;;
        down) state="down"; detail="$ROOT_REASON" ;;
    esac
    _STATUS_ROOT_SERVICE_LINES+=("$(_status_emit_service_json portal "$PORTAL_PORT_EFF" "$expected" "$PORTAL_LISTENING" "$PORTAL_HTTP_STATUS" "$owner" "$url" "$state" "$detail")")
}
_status_emit_portal_line

# _status_probe_service <name> <port> <extra-expected-cond 0/1> <do_health 0/1> <pgrep-pattern>
# Sets _STATUS_LAST_SERVICE_OK ("1" ok/skipped/unknown-tool, "0" down) so
# the caller can fold it into the "up" -> "degraded" downgrade.
_STATUS_LAST_SERVICE_OK="1"
_status_probe_service() {
    local name="$1" port="$2" extra_ok="$3" do_health="$4" pattern="$5"
    local expected="false"
    [[ "$ROOT_STATE" != "not-started" && "$extra_ok" == "1" ]] && expected="true"
    if [[ "$expected" != "true" ]]; then
        _STATUS_ROOT_SERVICE_LINES+=("$(_status_emit_service_json "$name" "$port" false "" "" unknown "" skipped "not applicable")")
        _STATUS_LAST_SERVICE_OK="1"
        return
    fi
    local listening="" http_status="" owner="unknown" url="" state="down" detail="" rc=2
    if declare -F svc_listening >/dev/null 2>&1; then
        svc_listening "$port"; rc=$?
    fi
    case "$rc" in
        0) listening="true" ;;
        1) listening="false" ;;
        *) listening="" ;;
    esac
    if [[ "$listening" == "true" ]]; then
        url="http://${DISPLAY_HOST}:${port}"
        [[ -n "$pattern" ]] && pgrep -f "$pattern" >/dev/null 2>&1 && owner="ours"
        if [[ "$do_health" == "1" && "$HAVE_CURL" == "1" ]]; then
            local raw
            raw="$(svc_http_status "http://${PROBE_HOST}:${port}/health" 0.7)"
            http_status="${raw%%$'\t'*}"
            [[ "$http_status" == "000" ]] && http_status=""
        fi
        state="up"
        _STATUS_LAST_SERVICE_OK="1"
    elif [[ "$listening" == "" ]]; then
        state="unknown"
        detail="undetectable — no lsof/ss"
        _STATUS_LAST_SERVICE_OK="1"   # A4/F30: a missing tool never degrades the verdict
    else
        state="down"
        detail="${name} not listening"
        _STATUS_LAST_SERVICE_OK="0"
    fi
    _STATUS_ROOT_SERVICE_LINES+=("$(_status_emit_service_json "$name" "$port" true "$listening" "$http_status" "$owner" "$url" "$state" "$detail")")
}

_STATUS_DEGRADED=()
_status_extended=0
[[ "$PROBE_LEVEL" == "extended" ]] && _status_extended=1

_status_probe_service memory "$LANCE_PORT" 1 "$_status_extended" "uvicorn.*arail\.memory_service.*--port ${LANCE_PORT}"
[[ "$_STATUS_LAST_SERVICE_OK" == "0" ]] && _STATUS_DEGRADED+=("memory")

_status_mlx_applicable=0
[[ "${MODEL_BACKEND:-auto}" == "mlx" ]] && _status_mlx_applicable=1
_status_probe_service mlx "${MLX_OPENAI_PORT:-11435}" "$_status_mlx_applicable" "$_status_extended" "uvicorn.*arail\.mlx_openai_server.*--port ${MLX_OPENAI_PORT:-11435}"
[[ "$_status_mlx_applicable" == "1" && "$_STATUS_LAST_SERVICE_OK" == "0" ]] && _STATUS_DEGRADED+=("mlx")

# Terminal/Notebook/IDE: expected only when the binary is installed AND
# launchd does not itself supervise it (install-daemon.sh:9-16 — launchd
# deliberately does not manage these, so daemon mode makes them N/A here,
# not "down").
_status_ttyd_applicable=0
{ command -v ttyd >/dev/null 2>&1 && [[ "$DAEMON_ACTIVE" != "1" ]]; } && _status_ttyd_applicable=1
_status_probe_service terminal "${TERMINAL_PORT:-7681}" "$_status_ttyd_applicable" 0 "ttyd"
[[ "$_status_ttyd_applicable" == "1" && "$_STATUS_LAST_SERVICE_OK" == "0" ]] && _STATUS_DEGRADED+=("terminal")

_status_jupyter_applicable=0
{ command -v jupyter >/dev/null 2>&1 && [[ "$DAEMON_ACTIVE" != "1" ]]; } && _status_jupyter_applicable=1
_status_probe_service notebook "${NOTEBOOK_PORT:-8888}" "$_status_jupyter_applicable" 0 "jupyter-lab"
[[ "$_status_jupyter_applicable" == "1" && "$_STATUS_LAST_SERVICE_OK" == "0" ]] && _STATUS_DEGRADED+=("notebook")

_status_ide_applicable=0
{ command -v code-server >/dev/null 2>&1 && [[ "$DAEMON_ACTIVE" != "1" ]]; } && _status_ide_applicable=1
_status_probe_service ide "${IDE_PORT:-8443}" "$_status_ide_applicable" 0 "code-server"
[[ "$_status_ide_applicable" == "1" && "$_STATUS_LAST_SERVICE_OK" == "0" ]] && _STATUS_DEGRADED+=("ide")

if [[ "$ROOT_STATE" == "up" && ${#_STATUS_DEGRADED[@]} -gt 0 ]]; then
    ROOT_STATE="degraded"
    ROOT_REASON="degraded: $(IFS=,; echo "${_STATUS_DEGRADED[*]}")"
fi

ROOT_SERVICES_JSON="$(printf '%s\n' "${_STATUS_ROOT_SERVICE_LINES[@]:-}" | _status_json_lines_to_array)"

# ── External: Ollama (machine-shared; NEVER "expected" — reported, never
# gated on our own liveness, ARCHITECTURE.md §7.1's own table) ──────────
OLLAMA_URL="http://${OLLAMA_HOST:-127.0.0.1:11434}"
OLLAMA_REACHABLE="false"
if [[ "$PROBE_LEVEL" != "none" && "$HAVE_CURL" == "1" ]]; then
    curl -sf -m 0.7 "${OLLAMA_URL}/api/version" >/dev/null 2>&1 && OLLAMA_REACHABLE="true"
fi
_status_ollama_pidfile="${ARAIL_DATA_DIR:-${LAB_ROOT:-lab}/data}/.ollama-started-by-arail.pid"
OLLAMA_MANAGED="false"
[[ -f "$_status_ollama_pidfile" ]] && OLLAMA_MANAGED="true"

WARNINGS_JSON="$(printf '%s\n' "${STATUS_WARNINGS[@]:-}" | python3 -c '
import json, sys
print(json.dumps([l.rstrip("\n") for l in sys.stdin if l.strip()]))
')"

# ── DB visibility (sprints/2026-08-10-arail2-persistence-instantiated
# §4.4) — read-only (apply=False, never creates what it checks), one row
# per resolved root (root ∪ registry ∪ on-disk-unregistered). A local
# file read only — permitted under --no-probe, never skipped by either
# probe flag, budgeted <50ms/root (test 32 measured ~2ms). Additive only:
# this bash variable feeds the python doc-builder below, which decides
# per-mode whether to expose it (--json=instances must stay untouched).
# REVIEW.md BLOCK-3: this collector used to end in `2>/dev/null || echo
# '{}'` — ANY failure (ImportError, a broken interpreter, a traceback)
# became `{}`, which renders as `"db": null` with no warning, no
# verdict.reasons entry, and no exit-code effect. Reproduced live: the
# DB subsystem failed to import entirely and status said nothing about
# it. stderr is now captured (not discarded) and a non-zero exit / empty
# / unparseable output is reported as a collector failure — DB_JSON
# stays "{}" (nothing to render per-root), but DB_COLLECTOR_FAILED=1
# carries the fact of the failure into the doc-builder below, which
# turns it into a warning + a verdict.reasons entry, and degrades any
# currently-live lab to 3 (never promoting a not-running lab's 4 to 3 —
# same liveness gate every other db-driven degrade already uses).
DB_JSON="{}"
DB_COLLECTOR_FAILED=0
DB_COLLECTOR_ERROR=""
if [[ -d "$REPO_ROOT/.venv" ]]; then
    _db_err_file="$(mktemp)"
    _db_out="$(cd "$REPO_ROOT" && source .venv/bin/activate && \
        inst_resolve_data_dirs | python3 -c '
import json
import sys
from arail.dbspec.ensure import ensure_db

out = {}
for line in sys.stdin:
    line = line.rstrip("\n")
    if not line:
        continue
    slug, data_dir, origin = line.split("\t")
    report = ensure_db(data_dir, apply=False)
    out[slug] = {
        "origin": origin,
        "data_dir": data_dir,
        "db": {
            "state": report.state, "version": report.version,
            "spec_version": report.spec_version, "present": report.present,
            "detail": report.detail,
        },
    }
print(json.dumps(out))
' 2>"$_db_err_file")"
    _db_rc=$?
    if [[ "$_db_rc" == "0" && -n "$_db_out" ]] \
        && python3 -c 'import json,sys; json.loads(sys.argv[1])' "$_db_out" >/dev/null 2>&1; then
        DB_JSON="$_db_out"
    else
        DB_COLLECTOR_FAILED=1
        DB_COLLECTOR_ERROR="$(tr '\n' ' ' < "$_db_err_file" | tr -s ' ' | cut -c1-300)"
        [[ -z "$DB_COLLECTOR_ERROR" ]] && DB_COLLECTOR_ERROR="exit ${_db_rc}, no output"
    fi
    rm -f "$_db_err_file"
fi
export DB_JSON DB_COLLECTOR_FAILED DB_COLLECTOR_ERROR

# ── Models (sprints/2026-10-20-boot-model-autoresolve) — the last-resolved
# slot A/B cache, read from model_defaults.yaml. A local file read: no
# network, no model load, permitted under --no-probe. Additive: a missing
# or unreadable file just means no `models` key.
MODELS_JSON="$(ARAIL_MODEL_DEFAULTS_FILE="${ARAIL_MODEL_DEFAULTS_FILE:-$REPO_ROOT/model_defaults.yaml}" python3 -c '
import json
try:
    from arail import model_defaults as md
    d = md.read()
    r = d.get("resolved") or {}
    w = r.get("warm") or {}
    out = {}
    for k in ("a", "b"):
        s = r.get(k) if isinstance(r.get(k), dict) else {}
        out[k] = {"model": s.get("model"), "source": s.get("source"), "warm": w.get(k)}
    print(json.dumps(out if r else {}))
except Exception:
    print("{}")
' 2>/dev/null || echo '{}')"
export MODELS_JSON

GENERATED_AT="$(python3 -c 'import datetime; print(datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))')"
PROVISIONED="false"
[[ -d .venv ]] && PROVISIONED="true"

MODE="human"
[[ "$STATUS_JSON_MODE" == "full" ]] && MODE="json"
[[ "$STATUS_JSON_MODE" == "instances" ]] && MODE="instances"

FACTS_JSON="$(python3 -c '
import json, sys
keys = ["mode","checkout","provisioned","lab_name","lab_tier","lab_mode","bind",
        "probe_level","daemon_active","plists_installed","root_state","root_reason",
        "portal_port","ollama_url","ollama_reachable","ollama_managed",
        "registry_unreadable","quiet","http_timeout_ms","generated_at"]
print(json.dumps(dict(zip(keys, sys.argv[1:]))))
' "$MODE" "$REPO_ROOT" "$PROVISIONED" "$LAB_NAME" "$LAB_TIER_EFF" "$LAB_MODE_EFF" "$BIND" \
  "$PROBE_LEVEL" "$DAEMON_ACTIVE" "$PLISTS_INSTALLED" "$ROOT_STATE" "$ROOT_REASON" \
  "$PORTAL_PORT_EFF" "$OLLAMA_URL" "$OLLAMA_REACHABLE" "$OLLAMA_MANAGED" \
  "$REGISTRY_UNREADABLE" "$STATUS_QUIET" "700" "$GENERATED_AT")"

export FACTS_JSON INSTANCES_JSON ROOT_SERVICES_JSON AGENTS_JSON WARNINGS_JSON DB_JSON DB_COLLECTOR_FAILED DB_COLLECTOR_ERROR
export GREEN BOLD YELLOW DIM RESET

# ── The one document-builder + two renderers (ARCHITECTURE.md §4.1, §7.3,
# §12.1) — a single-quoted heredoc (no bash interpolation at all) reading
# every fact from the environment, so nothing here can drift from what
# --json prints; sys.exit() carries the verdict code all the way out.
python3 <<'PYEOF'
import json
import os
import re
import sys


def _b(v):
    return v == "true" or v == "1"


# TEST_REPORT.md Q5: a World bundle is MADE to be shared (world-forge /
# world-mount) — manifest.display_name is not the operator's own text, it
# arrives verbatim from whoever built the bundle. Printed raw, it can carry
# CSI/CR/LF/BEL and forge a fake extra row on a real terminal, even piped
# (`cat -v` reveals it). The --json renderer already escapes control bytes
# (json.dumps) — this is the human renderer's equivalent, at the render
# boundary only (the stored record stays faithful; only what reaches the
# terminal is sanitised). Strips C0 controls (incl. ESC/CR/LF/BEL) and C1
# controls; everything else (including non-BMP text) passes through.
_CONTROL_BYTES_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# Stripping control bytes alone still leaves the PLAIN TEXT of a hostile
# display_name intact — a bundle author can pad an ordinary-looking row
# fragment (no ESC/CR needed) into the name and have it print inline right
# after the real one. Cap the length too: this is the same ~18-column
# width the table already formats the name field to (`{name:<18}`), so
# nothing legible is lost that the column wasn't already going to
# misalign on, and it is short enough that a forged row fragment cannot
# fit inside it.
_DISPLAY_NAME_MAX = 32


def _sanitize_for_terminal(s):
    if not isinstance(s, str):
        return s
    s = _CONTROL_BYTES_RE.sub("", s)
    if len(s) > _DISPLAY_NAME_MAX:
        s = s[: _DISPLAY_NAME_MAX - 1] + "…"
    return s


facts = json.loads(os.environ["FACTS_JSON"])
instances = json.loads(os.environ["INSTANCES_JSON"])
root_services = json.loads(os.environ["ROOT_SERVICES_JSON"])
agents = json.loads(os.environ["AGENTS_JSON"])
warnings = json.loads(os.environ["WARNINGS_JSON"])
db_by_slug = json.loads(os.environ.get("DB_JSON") or "{}")
try:
    models_info = json.loads(os.environ.get("MODELS_JSON") or "{}")
except ValueError:
    models_info = {}
db_collector_failed = os.environ.get("DB_COLLECTOR_FAILED") == "1"
db_collector_error = os.environ.get("DB_COLLECTOR_ERROR") or ""

# Only these four states are "needs attention" per ARCHITECTURE.md §4.4's
# exit-code mapping table — "unavailable" (no spec/schema/migrations at
# all, e.g. a stripped-down fork per BLUEPRINTS.md) is deliberately NOT
# in this set: a checkout the relational-store feature was never even
# vendored into is not "the dependent service is down", it's "this
# feature does not apply here" — degrading a live lab's status over that
# would be a false alarm this sprint's own test fixtures (which do not
# ship a spec/ tree) would otherwise trip on every existing scenario.
_DB_DEGRADING_STATES = {"pending", "blocked", "ahead", "diverged"}

# ASK-6 (REVIEW3.md / TEST_REPORT.md): BLOCK-5's suppression (below) must be
# keyed on the db STATE, not on the data_root_missing flag. "pending" (and
# "unavailable") are states DERIVED FROM the missing directory — there is
# nothing there, of course the db reads pending — and suppressing those is
# correct (see BLOCK-5). "diverged"/"blocked"/"ahead" are facts about the
# CHECKOUT (a tampered migration ledger, a corrupt file, a newer schema
# version) that remain true regardless of whether the data root happens to
# exist — gating them on data_root_missing swallowed a tampered-ledger
# report end to end (reproduced: verdict.reasons empty, exit 0, human view
# silent, on a checkout whose committed SQL had been altered).
_DB_SUPPRESSIBLE_WHEN_ROOT_MISSING = {"pending", "unavailable"}


def _suppress_for_missing_root(row_db, data_root_missing):
    """None if row_db should be suppressed (state is one of the "there's
    nothing there" states AND the data root is confirmed missing);
    row_db unchanged otherwise — in particular, "diverged"/"blocked"/
    "ahead" always pass through even when the data root is missing."""
    if (row_db and data_root_missing
            and row_db["state"] in _DB_SUPPRESSIBLE_WHEN_ROOT_MISSING):
        return None
    return row_db

mode = facts["mode"]
root_state = facts["root_state"]
root_reason = facts["root_reason"] or None

# ── Verdict (ARCHITECTURE.md §7.1's "verdict contribution" column,
# combined with the instance rows — T8's four asserted cases pin this
# combinator exactly: nothing running -> 4; one live instance -> 0; live
# instance + stale record -> 3; foreign root portal -> 3).
reasons = []
candidates = []

if facts["registry_unreadable"] == "1":
    reasons.append("registry:unreadable")

root_is_live = root_state in ("up", "degraded")

if root_state == "foreign":
    reasons.append(f"root:foreign:{facts['portal_port']}")
    candidates.append(3)
elif root_state == "up":
    candidates.append(0)
elif root_state == "degraded":
    reasons.append(f"root:degraded:{root_reason}")
    candidates.append(3)
elif root_state == "down":
    reasons.append("root:down")
    candidates.append(4)
# "not-started" is deliberately neutral — contributes nothing (§7.1).

# DB (sprints/2026-08-10-arail2-persistence-instantiated §4.4): a non-ok
# DB state only degrades the verdict for a root/instance that is actually
# LIVE. A stopped/never-started lab's not-yet-created DB must never turn
# "nothing running" (4) into "degraded" (3) — that promotion is
# explicitly forbidden by the spec.
_root_db = db_by_slug.get("__root__", {}).get("db")
if root_is_live and _root_db and _root_db["state"] in _DB_DEGRADING_STATES:
    reasons.append(f"root:db:{_root_db['state']}")
    candidates.append(3)

for row in instances:
    slug = row.get("slug", "?")
    state = row.get("state")
    if state == "unreadable":
        reasons.append(f"instance:{slug}:unreadable")
        candidates.append(3)
    elif state == "stale":
        reasons.append(f"instance:{slug}:stale")
        candidates.append(3)
    elif state == "live":
        if row.get("probe_mismatch_checkout"):
            reasons.append(f"instance:{slug}:checkout-mismatch")
            candidates.append(3)
        else:
            candidates.append(0)
        # BLOCK-5 (REVIEW2.md round 2) + ASK-6 fix: suppress only the
        # states DERIVED FROM the missing data root ("pending"/
        # "unavailable" — see _suppress_for_missing_root above); a
        # "diverged"/"blocked"/"ahead" fact about the CHECKOUT survives
        # even when the data root is missing. The row-level db object is
        # suppressed the same way in the instances_full augmentation
        # below.
        _row_db = _suppress_for_missing_root(
            db_by_slug.get(slug, {}).get("db"), row.get("data_root_missing"))
        if _row_db and _row_db["state"] in _DB_DEGRADING_STATES:
            reasons.append(f"instance:{slug}:db:{_row_db['state']}")
            candidates.append(3)

# REVIEW.md BLOCK-3: a collector failure (ImportError, a broken
# interpreter, any non-zero/unparseable exit from the DB subsystem) must
# never render as "db": null with silence — that is this sprint's own
# thesis (arail/provisioning.py: "declared and not instantiated is
# always a finding, never silence") broken by the surface that exists to
# enforce it. Always warned and always in verdict.reasons; only degrades
# the EXIT CODE for a lab that is actually live right now (same liveness
# gate every other db-driven degrade already uses — a collector failure
# on a lab that was never started still must not promote 4 to 3).
any_live = root_is_live or any(r.get("state") == "live" for r in instances)
if db_collector_failed:
    _detail = f": {db_collector_error}" if db_collector_error else ""
    warnings.append(f"db: status could not check the relational store{_detail}")
    reasons.append("db:collector-failed")
    if any_live:
        candidates.append(3)

if facts["registry_unreadable"] == "1":
    verdict_code = 1
    verdict_state = "error"
elif 3 in candidates:
    verdict_code = 3
    verdict_state = "degraded"
elif 0 in candidates:
    verdict_code = 0
    verdict_state = "ok"
else:
    verdict_code = 4
    verdict_state = "not-running"

verdict = {"code": verdict_code, "state": verdict_state, "reasons": reasons}

if mode == "instances":
    # Byte-compatible with the pre-WP5 --json output (docs/concurrent-worlds.md):
    # the bare rows array, nothing else on stdout. Built from the SAME
    # `instances` list the verdict above read — never the db-augmented
    # copy built below, so this mode is untouched by this sprint (F7).
    print(json.dumps(instances))
    sys.exit(verdict_code)

# ── --json / --json=full and the human renderer share one augmented copy
# of `instances` (db object + origin, additive-only keys) from here on.
# `--json=instances` above already exited using the pristine list, so
# nothing past this point can affect its byte-compatibility.
instances_full = []
for row in instances:
    row = dict(row)
    slug = row.get("slug", "?")
    entry = db_by_slug.get(slug)
    row["origin"] = (entry or {}).get("origin", "registry")
    # BLOCK-5 + ASK-6: suppress only "pending"/"unavailable" (derived from
    # the missing directory — "pending" is the wrong word for a database
    # that can't exist because its whole containing directory doesn't);
    # "diverged"/"blocked"/"ahead" are facts about the CHECKOUT and must
    # survive even when the data root is missing, or a tampered ledger
    # renders with no trace anywhere status has one (ASK-6, reproduced:
    # verdict.reasons empty, exit 0, human view silent).
    row["db"] = _suppress_for_missing_root(
        (entry or {}).get("db"), row.get("data_root_missing"))
    instances_full.append(row)

# ARCHITECTURE.md §4.3's "on-disk-unregistered is itself a finding": any
# slug resolve_data_dirs() found with origin="ondisk" that never appeared
# in the registry-driven `instances` rows at all gets its own minimal row
# here — additive-only, never touches the byte-compatible instances mode
# above, never counted toward the verdict (it isn't a "live" process by
# any definition this file has).
_known_slugs = {r.get("slug") for r in instances}
for slug, entry in sorted(db_by_slug.items()):
    if slug == "__root__" or slug in _known_slugs:
        continue
    if entry.get("origin") != "ondisk":
        continue
    instances_full.append({
        "slug": slug, "state": "unregistered", "origin": "ondisk",
        "data_dir": entry.get("data_dir", ""), "db": entry.get("db"),
    })

_root_entry = db_by_slug.get("__root__", {})

doc = {
    "schema": "arail.status/v2",
    "generated_at": facts["generated_at"],
    "checkout": facts["checkout"],
    "provisioned": _b(facts["provisioned"]),
    "lab": {"name": facts["lab_name"], "tier": facts["lab_tier"], "mode": facts["lab_mode"]},
    "bind": facts["bind"],
    "probe": {"level": facts["probe_level"], "http_timeout_ms": int(facts["http_timeout_ms"])},
    "warnings": warnings,
    "supervision": {
        "mode": "daemon" if _b(facts["daemon_active"]) else "foreground",
        "plists_installed": _b(facts["plists_installed"]),
        "agents": agents,
    },
    "instances": instances_full,
    "root": {
        "state": root_state, "reason": root_reason, "services": root_services,
        "origin": "root", "db": _root_entry.get("db"),
    },
    "external": {
        "ollama": {
            "url": facts["ollama_url"],
            "reachable": _b(facts["ollama_reachable"]),
            "managed_by_lab": _b(facts["ollama_managed"]),
        }
    },
    "verdict": verdict,
}
if models_info:
    doc["models"] = models_info     # additive; schema stays arail.status/v2

if mode == "json":
    print(json.dumps(doc, indent=2))
    sys.exit(verdict_code)

# ── Human renderer — same document, no ANSI codes here (bash supplies
# them via env, kept empty when gated off — F25) ─────────────────────────
GREEN = os.environ.get("GREEN", "")
BOLD = os.environ.get("BOLD", "")
YELLOW = os.environ.get("YELLOW", "")
DIM = os.environ.get("DIM", "")
RESET = os.environ.get("RESET", "")
quiet = facts["quiet"] == "1"


def ok(msg):
    print(f"  {GREEN}✓{RESET} {msg}")


def warn(msg):
    print(f"  {YELLOW}⚠{RESET} {msg}")


def dim(msg):
    print(f"  {DIM}{msg}{RESET}")


if not quiet:
    print("")
    print(f"{BOLD}{_sanitize_for_terminal(facts['lab_name'])} — Status{RESET}")
    print("")

print(f"  {BOLD}Instances{RESET}  (checkout: {facts['checkout']})")
print("")
if not instances:
    print("  (no World instances — see ./arailctl start --world <slug>)")
for r in instances_full:
    slug = r.get("slug", "?")
    state = r.get("state", "?")
    if state == "unregistered":
        # ARCHITECTURE.md §4.3: on-disk-unregistered is itself a finding.
        print(f"  ⚠ {slug:<10} on disk, no registry record "
              f"(re-register via ./arailctl install)")
        continue
    if state == "unreadable":
        print(f"  ✗ {slug:<10} unreadable (corrupt registry record — quarantined)")
        continue
    name = _sanitize_for_terminal(r.get("display_name")) or slug
    port = r.get("portal_port", "?")
    pid = r.get("portal_pid", "?")
    if state == "stale":
        print(f"  ✗ {slug:<10} {name:<18} stale (pid {pid} gone)")
        continue
    mismatch = r.get("probe_mismatch_checkout")
    if mismatch:
        print(f"  ⚠ {slug:<10} {name:<18} :{port}  pid {pid}  serving from a DIFFERENT checkout: {mismatch}")
    else:
        print(f"  ● {slug:<10} {name:<18} :{port}  pid {pid}")
    if r.get("data_root_missing"):
        print("                 ⚠ data root missing")
    data_dir = r.get("data_dir", "")
    if data_dir:
        print(f"                 data  {data_dir}")
    # Quiet boot's status-time equivalent (§4.4): print a db: line only
    # when this lab is up/live AND its db state isn't already ok.
    _r_db = r.get("db")
    if state == "live" and _r_db and _r_db["state"] in _DB_DEGRADING_STATES:
        print(f"                 db    {_r_db['state']}"
              + (f" — {_r_db['detail']}" if _r_db.get("detail") else ""))
print("")

if facts["provisioned"] == "true":
    ok(".venv present")
else:
    warn(".venv missing — run ./arailctl setup")

# ── Supervision ──────────────────────────────────────────────────────
if doc["supervision"]["mode"] == "daemon":
    print(f"  {BOLD}Supervision{RESET}: daemon (launchd)")
    for a in agents:
        label = a["label"]
        if a["pid"] is not None:
            ok(f"{label}: PID={a['pid']} LastExitStatus={a.get('last_exit_status')}")
        else:
            dim(f"{label}: installed, not loaded")
    print("")
elif doc["supervision"]["plists_installed"]:
    print(f"  {BOLD}Supervision{RESET}: foreground (start.sh) — launchd plists installed but inactive")
    print("")
else:
    print(f"  {BOLD}Supervision{RESET}: foreground (start.sh) — see ./arailctl install-daemon")
    print("")

if models_info:
    _ma, _mb = models_info.get("a") or {}, models_info.get("b") or {}

    def _mfmt(slot, m):
        name = m.get("model") or "(none)"
        extra = ", ".join(x for x in (m.get("source"),
                                      f"warm {m['warm']}" if m.get("warm") else None) if x)
        return f"{slot} {name}" + (f" ({extra})" if extra else "")
    print(f"  models: {_mfmt('fast', _ma)} · {_mfmt('deep', _mb)}")
    print("")

# ── Root lab (ARCHITECTURE.md §7.1 — "root lab: not started" replaces the
# five dim "not running" rows; a foreign checkout is named as such) ─────
if root_state == "not-started":
    print(f"  root lab: not started ({root_reason})")
elif root_state == "foreign":
    warn(f"root portal :{facts['portal_port']} is answered by a DIFFERENT checkout: {(root_reason or '').split(': ', 1)[-1]}")
    dim(f"lsof -iTCP:{facts['portal_port']} -sTCP:LISTEN")
elif root_state == "down":
    warn(f"root lab: {root_reason}")
else:
    # up / degraded — full per-service list, URL printed only when it
    # actually answered (gap 5's "URL block can lie" is retired here).
    label_map = {"portal": "Portal   ", "memory": "Memory   ", "mlx": "MLX API  ",
                 "terminal": "Terminal ", "notebook": "Notebook ", "ide": "IDE      "}
    for svc in root_services:
        name = svc["name"]
        if not svc["expected"]:
            continue
        label = label_map.get(name, name)
        if svc["state"] == "up":
            ok(f"{label}running on {svc['url']}")
        elif svc["state"] == "unknown":
            dim(f"{label}{svc.get('detail') or 'undetectable'}")
        else:
            warn(f"{label}{svc.get('detail') or 'not running'}")
    if root_state == "degraded":
        warn(f"root lab: {root_reason}")
    if root_is_live and _root_db and _root_db["state"] in _DB_DEGRADING_STATES:
        warn(f"db: {_root_db['state']}"
            + (f" — {_root_db['detail']}" if _root_db.get("detail") else ""))

print("")

if warnings:
    for w in warnings:
        warn(w)
    print("")

# Scheduler + Runtime-state sections stay in bash (below) — the scheduler
# needs a live HTTP round trip only worth making when the portal answered,
# and `du` sizes are deliberately excluded from the JSON document (T34:
# --json must stay inside the <2s budget; a `du -sh lab/pkb` walk can cost
# seconds on a large PKB).

sys.exit(verdict_code)
PYEOF
RENDER_RC=$?

if [[ "$MODE" == "human" ]]; then
    # ── Scheduler ──────────────────────────────────────────────────────
    # REVIEW.md n3: this probe still used $BIND directly (the pre-WP5
    # shape) instead of the loopback-normalized $PROBE_HOST (F29) every
    # other probe in this file already switched to — under
    # BIND_ADDR=0.0.0.0 it silently never fired, since probing
    # http://0.0.0.0:<port> is unreliable on macOS. Display still uses
    # $BIND (the configured address); only the probe target changes.
    if command -v curl >/dev/null && curl -sf "http://${PROBE_HOST}:${PORTAL_PORT_EFF}/api/jobs/state" >/dev/null 2>&1; then
        echo ""
        state_json="$(curl -sf "http://${PROBE_HOST}:${PORTAL_PORT_EFF}/api/jobs/state")"
        window=$(echo "$state_json"  | python3 -c "import sys,json;print(json.load(sys.stdin).get('label','?'))" 2>/dev/null || echo "?")
        halted=$(echo "$state_json"  | python3 -c "import sys,json;print(json.load(sys.stdin).get('halted','?'))" 2>/dev/null || echo "?")
        echo -e "  ${BOLD}Scheduler${RESET}"
        dim "window: ${window}"
        dim "halted: ${halted}"
    fi

    # ── lab/ state (skipped under --no-sizes or --quiet — T34/A: a `du`
    # walk can cost real seconds on a large PKB, and it is never in the
    # JSON document regardless of these flags) ───────────────────────
    if [[ "$STATUS_NO_SIZES" != "1" && "$STATUS_QUIET" != "1" ]]; then
        echo ""
        echo -e "  ${BOLD}Runtime state${RESET}"
        for d in lab/data lab/models lab/pkb; do
            if [[ -d "$d" ]]; then
                sz=$(du -sh "$d" 2>/dev/null | awk '{print $1}')
                dim "${d}  ${sz}"
            fi
        done
    fi
    echo ""
fi

# Prune stale records AFTER rendering, for every mode (ARCHITECTURE.md
# §2.5 — a status command that silently deletes what it just reported
# would be surprising; a `--json`/`--json=instances` run is a complete,
# self-contained invocation just like the human one).
inst_prune_all

exit "$RENDER_RC"

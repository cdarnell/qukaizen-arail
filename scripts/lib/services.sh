#!/usr/bin/env bash
# scripts/lib/services.sh — root-lab per-service readiness probing
# (sprints/2026-07-29-elite-cli/ARCHITECTURE.md §8.1).
#
# Boundary (load-bearing, do not blur): scripts/lib/instances.sh owns the
# World-instance registry and instance/daemon LIVENESS and remains the
# single source of truth for both. THIS file owns root-lab per-service
# READINESS probing — a thing instances.sh has never implemented. It must
# never re-derive daemon or instance liveness, and it must never grow a
# second `_port_in_use` — it obtains that from instances.sh's
# `inst_load_port_helpers` (which extracts the real one from setup.sh).
#
# Sourced (never executed) by scripts/start.sh and arailctl. Callers must
# set REPO_ROOT and source scripts/lib/instances.sh first (for
# inst_load_port_helpers) — this file does not source instances.sh itself
# to avoid a second copy of the "guard every source" discipline; callers
# already guard it (A2/F4).
#
# bash 3.2 only: no associative arrays, no readarray/mapfile, no
# ${var,,}/${var^^}, no local -n / declare -A. Every function here is
# `$( )`-safe under `set -euo pipefail` and never aborts the caller — a
# missing tool (curl/lsof/ss) is reported as a distinct "undetectable"
# state, never as "down" (A4, F30). No function here ever calls
# kill/pkill/pgrep (T31) or touches the instance registry or secrets.env
# (T30) — this file only ever reads loopback HTTP/TCP state.
set -uo pipefail 2>/dev/null || true  # tolerate being sourced under a caller's own `set`

# svc_probe_host <bind> — the host to actually PROBE (loopback-normalized;
# F29). BIND_ADDR=0.0.0.0/::/empty means "listen on every interface", which
# is not itself a reachable probe target — always probe loopback instead,
# while the caller keeps displaying the configured bind for the URL. Never
# fails.
svc_probe_host() {
    local bind="${1:-}"
    case "$bind" in
        ""|0.0.0.0|"::"|"[::]") printf '127.0.0.1' ;;
        *) printf '%s' "$bind" ;;
    esac
    return 0
}

# svc_listening <port> — 0 listening, 1 not listening, 2 undetectable (no
# lsof/ss on this box — A4/F30: a missing tool is never "down"). Requires
# _port_in_use already loaded into this shell via
# instances.sh:inst_load_port_helpers (never re-implemented here).
svc_listening() {
    local port="$1"
    if ! command -v lsof >/dev/null 2>&1 && ! command -v ss >/dev/null 2>&1; then
        return 2
    fi
    if ! declare -F _port_in_use >/dev/null 2>&1; then
        return 2
    fi
    if _port_in_use "$port"; then
        return 0
    fi
    return 1
}

# svc_http_status <url> [timeout-seconds] — prints "<code>\t<body>" on
# stdout; code "000" means no answer (refused/timeout/absent process —
# never distinguished from "curl not installed" here; callers that care
# about that distinction check `command -v curl` themselves, same pattern
# already used by instances.sh:inst_probe_matches and start.sh's stage
# [6/8]). Never aborts the caller under set -e.
svc_http_status() {
    local url="$1" timeout="${2:-0.7}"
    local raw status body
    # -f only affects whether the BODY is kept on a non-2xx response; curl
    # still writes the -w format string on a failed/refused request, so a
    # real HTTP error status is distinguishable from no answer at all
    # (the exact trick scripts/start.sh's stage [6/8] already relies on).
    raw="$(curl -sf -m "$timeout" -w '\n%{http_code}' "$url" 2>/dev/null || true)"
    status="${raw##*$'\n'}"
    body="${raw%$'\n'*}"
    [[ -n "$status" ]] || status="000"
    [[ "$body" == "$raw" ]] && body=""  # no newline found at all -> no body
    printf '%s\t%s\n' "$status" "$body"
    return 0
}

# svc_wait_listening <port> <deciseconds> [pid] — poll svc_listening at
# 0.1s ticks until it reports listening (0) or the cap is reached. If <pid>
# is given and dies mid-wait, returns 1 immediately (no point polling a
# port a dead process was never going to bind). Returns 0/1; an
# undetectable environment (svc_listening -> 2) is treated as "not yet"
# for the purpose of the loop but the caller can re-derive "unknown" by
# calling svc_listening once more itself if it needs to distinguish it.
svc_wait_listening() {
    local port="$1" cap_ds="$2" pid="${3:-}"
    local waited=0 rc
    while (( waited < cap_ds )); do
        if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
            return 1
        fi
        rc=0
        svc_listening "$port" || rc=$?
        [[ "$rc" == "0" ]] && return 0
        sleep 0.1
        waited=$((waited + 1))
    done
    return 1
}

# svc_wait_http_ready <url> <deciseconds> [pid] — poll until HTTP 200,
# printing the LAST non-"000" status seen on stdout for diagnostics (empty
# if none). Early-out (return 1) if <pid> dies mid-wait. Returns 0/1;
# returns 2 (distinct from the documented 0/1 — a defensive extension,
# never triggered when curl is present) only when curl itself is missing,
# so a caller can tell "genuinely not ready" apart from "could not probe at
# all" (A4) rather than silently spinning the full cap with no possible
# answer.
svc_wait_http_ready() {
    local url="$1" cap_ds="$2" pid="${3:-}"
    if ! command -v curl >/dev/null 2>&1; then
        return 2
    fi
    local waited=0 last_status="" raw status
    while (( waited < cap_ds )); do
        if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
            [[ -n "$last_status" ]] && printf '%s\n' "$last_status"
            return 1
        fi
        raw="$(svc_http_status "$url" 0.7)"
        status="${raw%%$'\t'*}"
        if [[ -n "$status" && "$status" != "000" ]]; then
            last_status="$status"
        fi
        if [[ "$status" == "200" ]]; then
            printf '%s\n' "$status"
            return 0
        fi
        sleep 0.1
        waited=$((waited + 1))
    done
    [[ -n "$last_status" ]] && printf '%s\n' "$last_status"
    return 1
}

# _svc_json_field <json-or-garbage> <field> — private, never raises;
# prints "" on anything that isn't a JSON object with that field (mirrors
# start.sh's _json_field / instances.sh's inst_record_field contract).
# Named distinctly (not `_json_field`) so this file has no naming
# dependency on start.sh's internals when sourced standalone (e.g. by
# arailctl for the daemon-branch readiness gate, which never sources
# start.sh).
_svc_json_field() {
    python3 -c '
import json, sys
try:
    data = json.loads(sys.argv[1])
    if not isinstance(data, dict):
        raise TypeError("not a JSON object")
    val = data.get(sys.argv[2], "")
except Exception:
    val = ""
print(val if val is not None else "")
' "$1" "$2"
}

# svc_identity_root <body> <expect_checkout> — 0 iff slug=="root" AND
# checkout==<expect_checkout> (A7: root has no token, so slug+checkout
# stands in for the instance path's token+checkout pair). The root-lab
# analogue of instances.sh's inst_probe_matches / start.sh's M1 gate — a
# foreign process squatting the port is never mistaken for our boot.
svc_identity_root() {
    local body="$1" expect_checkout="$2"
    local slug checkout
    slug="$(_svc_json_field "$body" slug)"
    [[ "$slug" == "root" ]] || return 1
    checkout="$(_svc_json_field "$body" checkout)"
    [[ -n "$checkout" && "$checkout" == "$expect_checkout" ]] || return 1
    return 0
}

# svc_portal_curl <data_dir> <curl args...> — curl a portal route with the lab's bearer token.
# (S4 portal auth, ARCHITECTURE.md C7/F3.) The token never appears in any process's argv, which
# `ps` shows to every user: the header goes in through a process substitution that only bash
# builtins (printf, $(<file)) write to, and curl reads it with `-H @file` (curl >= 7.55).
# Returns 77 without calling curl when the token file is missing or unreadable, so the caller can
# print "needs arailctl as the lab owner" instead of a bare failure.
svc_portal_curl() {
    local data_dir="$1"; shift
    local tok_file="$data_dir/portal.token"
    [[ -r "$tok_file" ]] || return 77
    curl -H @<(printf 'Authorization: Bearer %s\n' "$(<"$tok_file")") "$@"
}

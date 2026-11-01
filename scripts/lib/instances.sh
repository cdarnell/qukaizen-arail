#!/usr/bin/env bash
# scripts/lib/instances.sh — the single source of truth for World-instance
# paths, the instance registry, and liveness. See:
#   sprints/2026-07-28-concurrent-worlds/ARCHITECTURE.md §1, §2, §2.6
#
# Sourced (never executed) by arailctl, scripts/start.sh, scripts/status.sh,
# scripts/install-daemon.sh. Callers must set REPO_ROOT before sourcing —
# every path helper below is derived from it, never from CWD.
#
# Rule (ARCHITECTURE.md §2.6): after this sprint, the strings
#   ~/Library/LaunchAgents/io.arail.portal.plist
#   launchctl list io.arail.portal
# appear in exactly one place each — HERE, inside daemon_active() /
# daemon_plist_installed(). No other file in this repo should re-derive
# daemon liveness.
#
# This file only defines functions/vars. It never has side effects on
# source (no directory creation, no process spawn) — WP1 scope is "no
# caller changes yet"; callers opt in explicitly by invoking a function.
set -uo pipefail 2>/dev/null || true  # tolerate being sourced under a caller's own `set`

# ── Constants ─────────────────────────────────────────────────────────────

# Slug jail — MUST match src/arail/world_mount.py:141 (_SLUG_RE), verified.
INST_SLUG_RE='^[a-z0-9][a-z0-9-]*$'

# Port layout (ARCHITECTURE.md §3.4).
INST_PORT_BASE=8090
INST_PORT_BLOCK_SIZE=10
INST_PORT_PORTAL_OFFSET=0
INST_PORT_LANCE_OFFSET=4
INST_PORT_CEILING=9100          # hard stop: refuse any allocation >= this
INST_MAX_INSTANCES_DEFAULT=3

# Claims older than this many seconds are treated as abandoned (F6).
INST_CLAIM_STALE_SECONDS=120

# ── Path helpers ──────────────────────────────────────────────────────────
# All require REPO_ROOT to already be set (absolute) by the caller.

inst_root_dir() {
    printf '%s/lab/instances\n' "$REPO_ROOT"
}

inst_registry_dir() {
    printf '%s/registry.d\n' "$(inst_root_dir)"
}

inst_instance_dir() {
    local slug="$1"
    printf '%s/%s\n' "$(inst_root_dir)" "$slug"
}

inst_env_file() {
    local slug="$1"
    printf '%s/instance.env\n' "$(inst_instance_dir "$slug")"
}

inst_data_dir() {
    printf '%s/data\n' "$(inst_instance_dir "$1")"
}

inst_pkb_dir() {
    printf '%s/pkb\n' "$(inst_instance_dir "$1")"
}

inst_log_dir() {
    printf '%s/log\n' "$(inst_instance_dir "$1")"
}

inst_registry_file() {
    local slug="$1"
    printf '%s/%s.json\n' "$(inst_registry_dir)" "$slug"
}

inst_registry_bad_file() {
    local slug="$1"
    printf '%s/%s.json.bad\n' "$(inst_registry_dir)" "$slug"
}

inst_claim_file() {
    local slug="$1"
    printf '%s/%s.claim\n' "$(inst_registry_dir)" "$slug"
}

inst_valid_slug() {
    local slug="${1-}"
    [[ "$slug" =~ $INST_SLUG_RE ]]
}

# ── Registry: write ───────────────────────────────────────────────────────
# inst_write_record <slug> <json-payload>
# <json-payload> is a JSON *object string* (schema: ARCHITECTURE.md §2.1).
# Passed as argv (not interpolated into a shell command), parsed and
# re-serialised by python3, then written via tmp + os.replace — the same
# atomic-rename technique world_mount.py:692-699 already uses. Never
# hand-rolled `echo > file` JSON (ARCHITECTURE.md §2.2).
inst_write_record() {
    local slug="$1" payload="$2"
    local dir
    dir="$(inst_registry_dir)"
    mkdir -p "$dir"
    python3 -c '
import json, os, sys

slug, payload, dest_dir = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.loads(payload)
tmp = os.path.join(dest_dir, slug + ".json.tmp")
final = os.path.join(dest_dir, slug + ".json")
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
os.replace(tmp, final)
' "$slug" "$payload" "$dir"
}

# ── Registry: read ────────────────────────────────────────────────────────
# inst_read_record <slug>
# Prints the record JSON on stdout; returns 1 (silently, no output) if the
# file is absent. Corrupt JSON is quarantined to <slug>.json.bad and this
# returns 2 — never raises, never crashes a caller (F16, mirrors
# list_available_worlds()'s never-raises contract).
inst_read_record() {
    local slug="$1"
    local f
    f="$(inst_registry_file "$slug")"
    [[ -f "$f" ]] || return 1
    local out
    if out="$(python3 -c '
import json, sys
try:
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        # QA-6: valid JSON that is not an object ([1,2,3], "x", 42, null,
        # true) used to parse successfully here and be handed to callers
        # (inst_record_field, status.sh) that assume a dict, raising
        # AttributeError/TypeError downstream. Quarantine this class the
        # same way a JSON-parse failure already is — F16 makes no
        # distinction between "unparseable" and "not a record".
        raise TypeError("record is not a JSON object")
except Exception:
    sys.exit(1)
json.dump(data, sys.stdout)
' "$f" 2>/dev/null)"; then
        printf '%s' "$out"
        return 0
    fi
    # Corrupt or unparseable — quarantine, never block the caller.
    mv -f "$f" "$(inst_registry_bad_file "$slug")" 2>/dev/null || true
    return 2
}

# inst_record_field <json> <field>
# Pulls a single top-level field out of a record JSON string. Empty string
# (not an error) when the field is absent — callers already treat "" as
# "unknown" per the liveness predicate below.
inst_record_field() {
    local json="$1" field="$2"
    python3 -c '
import json, sys
try:
    data = json.loads(sys.argv[1])
    if not isinstance(data, dict):
        # QA-6: the try/except used to wrap json.loads only — a
        # valid-JSON-wrong-type record ([1,2,3], "x", 42, null, true)
        # parsed fine and then raised on data.get(...) OUTSIDE this
        # block, leaking a raw traceback under set -euo pipefail.
        raise TypeError("record is not a JSON object")
    val = data.get(sys.argv[2], "")
except Exception:
    val = ""
print(val if val is not None else "")
' "$json" "$field"
}

# ── Registry: list / prune ────────────────────────────────────────────────

# inst_list_slugs — every slug with a *.json file in the registry, one per
# line — READABLE OR NOT. REVIEW.md M7: this used to pre-filter via its
# OWN inst_read_record call, which quarantines a corrupt file to
# <slug>.json.bad as a side effect of merely checking readability — so by
# the time a caller (e.g. status.sh) did its OWN inst_read_record call to
# classify the row, the file was already gone and the slug had never been
# yielded at all. F16's "row rendered ✗ unreadable" was unreachable: a
# corrupt record vanished from `status` with NO message. Emitting every
# basename here and letting the caller classify (via its own
# inst_read_record call, which still quarantines — exactly once, not
# twice) makes that row reachable again.
inst_list_slugs() {
    local dir f
    dir="$(inst_registry_dir)"
    [[ -d "$dir" ]] || return 0
    for f in "$dir"/*.json; do
        [[ -e "$f" ]] || continue
        basename "$f" .json
    done
    return 0
}

# inst_ondisk_slugs — every lab/instances/<slug>/ directory that contains a
# data/ dir or an instance.env file, ONE PER LINE, regardless of whether it
# has a registry record. sprints/2026-08-10-arail2-persistence-instantiated
# §4.3: the operator's registry.d/ can be (was, measured) literally empty
# while five instance dirs exist on disk — inst_list_slugs() alone reaches
# zero of them. This is the raw on-disk enumeration; inst_resolve_data_dirs
# below is the union with the registry.
inst_ondisk_slugs() {
    local root d
    root="$(inst_root_dir)"
    [[ -d "$root" ]] || return 0
    for d in "$root"/*/; do
        [[ -d "$d" ]] || continue
        local slug
        slug="$(basename "$d")"
        [[ "$slug" == "registry.d" ]] && continue
        if [[ -d "${d}data" || -f "${d}instance.env" ]]; then
            printf '%s\n' "$slug"
        fi
    done
    return 0
}

# inst_resolve_data_dirs — the six-roots fix (shell mirror of
# arail.data_dirs.resolve_data_dirs). Emits one TSV line per row:
#   <slug>\t<data_dir>\t<origin>
# where origin is root|registry|ondisk. Union is never smaller than either
# input (F11); the root lab is always exactly one row. Mirrors
# src/arail/data_dirs.py's resolve_data_dirs() — a shared-fixture test
# (tests/test_data_dirs_resolve_shell_parity.py, if the .venv running this
# checkout has one) is expected to assert the two agree on the same
# on-disk fixture; see BUILD_LOG.md for what has and has not been run.
inst_resolve_data_dirs() {
    # No associative arrays (services.sh's portability rule: macOS system
    # bash is 3.2, no `declare -A`) — dedup via a plain newline list and
    # grep -Fxq instead.
    local root_data_dir="${ARAIL_DATA_DIR:-$REPO_ROOT/lab/data}"
    printf '__root__\t%s\troot\n' "$root_data_dir"

    local slug registry_slugs
    registry_slugs="$(inst_list_slugs)"
    while IFS= read -r slug; do
        [[ -n "$slug" ]] || continue
        printf '%s\t%s\tregistry\n' "$slug" "$(inst_data_dir "$slug")"
    done <<< "$registry_slugs"

    while IFS= read -r slug; do
        [[ -n "$slug" ]] || continue
        if printf '%s\n' "$registry_slugs" | grep -Fxq "$slug"; then
            continue  # already emitted as a registry row above
        fi
        printf '%s\t%s\tondisk\n' "$slug" "$(inst_data_dir "$slug")"
    done < <(inst_ondisk_slugs)
    return 0
}

# inst_prune <slug> — remove the registry record for <slug> iff it is
# stale (predicate steps 2/3 fail). Never touches lab/instances/<slug>/
# data (ARCHITECTURE.md §2.5). No-op, exit 0, if the record is alive or
# absent.
inst_prune() {
    local slug="$1"
    local f
    f="$(inst_registry_file "$slug")"
    [[ -f "$f" ]] || return 0
    if inst_alive "$slug"; then
        return 0
    fi
    rm -f "$f"
    return 0
}

# inst_prune_all — run inst_prune over every registered slug. Used by
# `status` (side effect of the read path) — never by `start` for a slug
# other than its own target.
inst_prune_all() {
    local slug
    while IFS= read -r slug; do
        [[ -n "$slug" ]] && inst_prune "$slug"
    done < <(inst_list_slugs)
    return 0
}

# ── Last launched target (the picker's memory) ────────────────────────────
# One file per checkout, next to registry.d/ because the picker already
# reads that directory. It records what the operator last STARTED
# successfully — root lab or a World — so a bare `start` can default to it
# instead of always landing on option 0.
#
# This is a preference, not state: unlike a registry record there is
# nothing to diagnose in a corrupt one, so it is treated as absent rather
# than quarantined, and every write is best-effort. A preference file must
# never be able to fail a start.
inst_last_target_file() {
    printf '%s\n' "$(inst_root_dir)/last-target.json"
}

# inst_write_last_target <kind> [slug]
#   <kind> is "root" or "world"; <slug> is required for "world", ignored
#   for "root". Same tmp + os.replace atomicity as inst_write_record.
#   ALWAYS returns 0 — callers run this after a lab is already up.
inst_write_last_target() {
    local kind="${1-}" slug="${2-}"
    local dir
    dir="$(inst_root_dir)"
    case "$kind" in
        root)  slug="" ;;
        world) [[ -n "$slug" ]] && inst_valid_slug "$slug" || return 0 ;;
        *)     return 0 ;;
    esac
    mkdir -p "$dir" 2>/dev/null || return 0
    python3 -c '
import datetime, json, os, sys

kind, slug, dest_dir = sys.argv[1], sys.argv[2], sys.argv[3]
doc = {
    "schema": "arail.last-target/v1",
    "kind": kind,
    "slug": slug or None,
    "at": datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z"),
}
tmp = os.path.join(dest_dir, "last-target.json.tmp")
final = os.path.join(dest_dir, "last-target.json")
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(doc, fh, indent=2)
    fh.write("\n")
os.replace(tmp, final)
' "$kind" "$slug" "$dir" 2>/dev/null || true
    return 0
}

# inst_read_last_target — prints "root", or "world<TAB><slug>".
# Returns 1 (silently) when the file is absent, unreadable, unparseable,
# not an object, carries an unknown kind, or names an invalid slug. The
# slug is re-validated against INST_SLUG_RE here so a hand-edited file can
# never inject a path fragment into the caller's start command.
inst_read_last_target() {
    local f out kind slug
    f="$(inst_last_target_file)"
    [[ -f "$f" ]] || return 1
    out="$(python3 -c '
import json, sys
try:
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise TypeError("last-target is not a JSON object")
    kind = data.get("kind")
    slug = data.get("slug") or ""
    if kind == "root":
        print("root")
    elif kind == "world" and isinstance(slug, str) and slug:
        print("world\t" + slug)
    else:
        raise ValueError("unusable last-target")
except Exception:
    sys.exit(1)
' "$f" 2>/dev/null)" || return 1
    kind="${out%%$'\t'*}"
    slug=""
    [[ "$out" == *$'\t'* ]] && slug="${out#*$'\t'}"
    if [[ "$kind" == "world" ]]; then
        inst_valid_slug "$slug" || return 1
        printf 'world\t%s\n' "$slug"
        return 0
    fi
    printf 'root\n'
    return 0
}

# ── Liveness predicate (ARCHITECTURE.md §2.3) ─────────────────────────────
# inst_alive <slug> [--probe]
# Returns 0 iff all of steps 1-3 hold (default), or all of 1-4 with --probe.
#   1. registry.d/<slug>.json exists and parses
#   2. kill -0 <portal_pid> succeeds
#   3. ps -p <portal_pid> -o command= matches uvicorn.*arail\.portal\.app
#      AND contains --port <portal_port>  (anti PID-reuse)
#   4. (--probe only) GET /api/instance token+checkout match the record
#      (anti wrong-checkout). Implemented as a helper other WPs (start's
#      attach check, stop's confirmation, status --probe) call explicitly —
#      kept here since it is part of the one predicate definition.
inst_alive() {
    local slug="$1" mode="${2:-}"
    local rec
    rec="$(inst_read_record "$slug" 2>/dev/null)" || return 1

    local pid port
    pid="$(inst_record_field "$rec" portal_pid)"
    port="$(inst_record_field "$rec" portal_port)"
    [[ -n "$pid" ]] || return 1

    kill -0 "$pid" 2>/dev/null || return 1

    local cmd
    cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    [[ -n "$cmd" ]] || return 1
    [[ "$cmd" =~ uvicorn.*arail\.portal\.app ]] || return 1
    if [[ -n "$port" ]]; then
        [[ "$cmd" == *"--port $port"* || "$cmd" == *"--port=$port"* ]] || return 1
    fi

    if [[ "$mode" == "--probe" ]]; then
        inst_probe_matches "$slug" "$rec" || return 1
    fi

    return 0
}

# inst_probe_matches <slug> <record-json> — step 4 in isolation, so start's
# attach check and status --probe can call it without paying for steps 1-3
# twice. GET /api/instance is a WP6 (portal) deliverable; until it exists
# this returns 1 (fail closed — never claim a checkout match we didn't
# verify).
inst_probe_matches() {
    local slug="$1" rec="$2"
    local bind port token checkout expect_token expect_checkout url response
    bind="$(inst_record_field "$rec" bind)"
    port="$(inst_record_field "$rec" portal_port)"
    expect_token="$(inst_record_field "$rec" token)"
    expect_checkout="$(inst_record_field "$rec" checkout)"
    [[ -n "$bind" && -n "$port" ]] || return 1
    command -v curl >/dev/null 2>&1 || return 1
    url="http://${bind}:${port}/api/instance"
    response="$(curl -sf -m 0.7 "$url" 2>/dev/null)" || return 1
    [[ -n "$response" ]] || return 1
    token="$(inst_record_field "$response" token)"
    checkout="$(inst_record_field "$response" checkout)"
    [[ -n "$token" && "$token" == "$expect_token" ]] || return 1
    [[ -n "$checkout" && "$checkout" == "$expect_checkout" ]] || return 1
    return 0
}

# inst_any_alive — prints the slug of the first live instance found (if
# any) on stdout and returns 0; returns 1 with no output when none are
# alive. Used by install-daemon.sh's refusal guard (ARCHITECTURE.md §2.6:
# "inst_any_alive() (registry-driven) || pgrep fallback for a legacy root
# lab; message names which instance blocks").
inst_any_alive() {
    local slug
    while IFS= read -r slug; do
        [[ -n "$slug" ]] || continue
        if inst_alive "$slug"; then
            printf '%s\n' "$slug"
            return 0
        fi
    done < <(inst_list_slugs)
    return 1
}

# ── Daemon-mode predicate (ARCHITECTURE.md §2.6, §4.4) ───────────────────
# daemon_plist_installed — plist FILE existence only (today's arailctl:195
# check). Kept as a separate, narrower helper because status.sh wants to
# print "installed, inactive" as distinct from "active".
daemon_plist_installed() {
    [[ -f "$HOME/Library/LaunchAgents/io.arail.portal.plist" ]]
}

# daemon_active — true iff the plist exists AND launchctl reports a live
# numeric PID for it. This is the actual liveness check; replaces the four
# disagreeing checks named in ARCHITECTURE.md §2.6's table. A loaded-but
# crash-looping agent (no PID line) is NOT active.
daemon_active() {
    [[ "$(uname -s)" == "Darwin" ]] || return 1
    daemon_plist_installed || return 1
    command -v launchctl >/dev/null 2>&1 || return 1
    local line
    line="$(launchctl list io.arail.portal 2>/dev/null | grep -E '"PID"[[:space:]]*=' || true)"
    [[ -n "$line" ]] || return 1
    # Extract the numeric value; a malformed/blank PID is not a live daemon.
    local pid
    pid="$(printf '%s' "$line" | grep -oE '[0-9]+' | head -n1 || true)"
    [[ -n "$pid" ]]
}

# ── First-boot scaffold (ARCHITECTURE.md §1.1) ─────────────────────────────

# inst_scaffold_instance_root <slug> — mkdir the fixed instance tree.
# Idempotent: safe to call on every boot, not just the first.
inst_scaffold_instance_root() {
    local slug="$1"
    local root
    root="$(inst_instance_dir "$slug")"
    mkdir -p "$(inst_data_dir "$slug")" \
             "$(inst_pkb_dir "$slug")/sources" \
             "$(inst_pkb_dir "$slug")/notes" \
             "$(inst_log_dir "$slug")"
    # QA-10 / ARCHITECTURE.md §7: data/ will hold secrets.env — a bare
    # mkdir -p leaves its mode at the operator's umask (commonly 0755).
    # secrets.env itself is still written 0600, so this is defence in
    # depth, not today's actual disclosure surface.
    chmod 0700 "$(inst_data_dir "$slug")" 2>/dev/null || true
    printf '%s\n' "$root"
}

# ── Reuse of scripts/setup.sh's proven helpers (ARCHITECTURE.md §3.4, §10) ──
# setup.sh cannot be `source`d directly — it unconditionally runs `main "$@"`
# at EOF — so the specific functions we need are extracted (awk range over
# the named function's body) and eval'd into THIS shell, never copied. Same
# extraction technique tests/test_reset_paths.py and
# tests/shell_source_safety_driver.sh already use to keep shell logic from
# forking into a second copy that can drift.
inst_load_setup_functions() {
    local setup_sh="$REPO_ROOT/scripts/setup.sh"
    [[ -f "$setup_sh" ]] || return 1
    local name fn_body
    for name in "$@"; do
        declare -F "$name" >/dev/null 2>&1 && continue  # already loaded — idempotent
        fn_body="$(awk "/^${name}\\(\\)/,/^}/" "$setup_sh")"
        [[ -n "$fn_body" ]] || return 1
        eval "$fn_body"
    done
}

# inst_load_port_helpers — pulls in the real _port_in_use / _find_free_port
# from setup.sh:298-329 (verified). ARCHITECTURE.md §10 WP3 scope: "export
# _port_in_use/_find_free_port for reuse — do not copy them."
inst_load_port_helpers() {
    inst_load_setup_functions _port_in_use _find_free_port
}

# inst_load_env_writer — pulls in the real _set_env_var from setup.sh:1539,
# the same shell-safe-quoting function tests/shell_source_safety_driver.sh
# already proves against hostile input (embedded $, backticks, quotes,
# command substitution). The env pack reuses that exact discipline rather
# than re-deriving quoting rules (ARCHITECTURE.md §1.2).
inst_load_env_writer() {
    inst_load_setup_functions _set_env_var
}

# ── Port allocation (ARCHITECTURE.md §3.4) ──────────────────────────────────
# Block k, base B_k = INST_PORT_BASE + k*INST_PORT_BLOCK_SIZE. Portal =
# B_k+INST_PORT_PORTAL_OFFSET, Lance = B_k+INST_PORT_LANCE_OFFSET. Offsets
# 1-3/5-9 are reserved (future per-instance ttyd/notebook/IDE/MLX) so a
# later sprint needs no renumbering.

# inst_port_excluded <port> — the explicit exclusion list from §3.4. None of
# these fall inside 8090-8114 given the ceiling below, but the exclusion is
# spelled out in code (not just "implied by the range") so a future range
# change can't silently collide.
inst_port_excluded() {
    case "$1" in
        8443|8888|7681|7414|11434|11435) return 0 ;;
        *) return 1 ;;
    esac
}

# inst_ports_registered — every portal_port/lance_port value already pinned
# to ANY instance record (live or not — a stopped-but-still-registered
# instance still owns its ports; a genuinely stale record is pruned by
# `status`/`start`'s own prune step, not by allocation). One per line.
inst_ports_registered() {
    local dir f slug rec
    dir="$(inst_registry_dir)"
    [[ -d "$dir" ]] || return 0
    for f in "$dir"/*.json; do
        [[ -e "$f" ]] || continue
        slug="$(basename "$f" .json)"
        rec="$(inst_read_record "$slug" 2>/dev/null)" || continue
        inst_record_field "$rec" portal_port
        inst_record_field "$rec" lance_port
    done
    return 0
}

# inst_allocate_ports — first boot only. Walks k=0,1,2… and takes the first
# block where both ports are free (bind-test via _port_in_use), not
# excluded, and not already claimed by another instance's registry record.
# Prints "<portal_port> <lance_port>" on success. Hard stop: refuses (exit
# 1, message on stderr) at or above INST_PORT_CEILING, even under a raised
# ceiling elsewhere — this function enforces its own floor regardless of
# caller-supplied limits (ARCHITECTURE.md §3.4: "Hard stop: refuse any
# allocation at or above 9100, even under a raised LAB_MAX_INSTANCES").
inst_allocate_ports() {
    inst_load_port_helpers || {
        echo "inst_allocate_ports: could not load port helpers from scripts/setup.sh" >&2
        return 1
    }
    local taken
    taken="$(inst_ports_registered)"
    local k=0 base portal lance
    while true; do
        base=$(( INST_PORT_BASE + k * INST_PORT_BLOCK_SIZE ))
        if (( base >= INST_PORT_CEILING )); then
            echo "inst_allocate_ports: no free port block below ${INST_PORT_CEILING} (checked $((k)) block(s) starting at ${INST_PORT_BASE}) — stop an existing instance or free a port" >&2
            return 1
        fi
        portal=$(( base + INST_PORT_PORTAL_OFFSET ))
        lance=$(( base + INST_PORT_LANCE_OFFSET ))
        if inst_port_excluded "$portal" || inst_port_excluded "$lance"; then
            k=$((k + 1)); continue
        fi
        if printf '%s\n' "$taken" | grep -qx "$portal" || printf '%s\n' "$taken" | grep -qx "$lance"; then
            k=$((k + 1)); continue
        fi
        if _port_in_use "$portal" || _port_in_use "$lance"; then
            k=$((k + 1)); continue
        fi
        printf '%s %s\n' "$portal" "$lance"
        return 0
    done
}

# ── Env pack writer (ARCHITECTURE.md §1.2) ──────────────────────────────────
# inst_write_env_pack <slug> KEY1 VALUE1 [KEY2 VALUE2 ...]
# Writes lab/instances/<slug>/instance.env from scratch (the pack is written
# once at first boot and read verbatim after — never incrementally patched;
# see §1.2) using the exact shell-safe quoting discipline
# tests/shell_source_safety_driver.sh already pins for lab.conf and
# blueprint .env files. Caller decides the key set and order — WP4's stage 3
# passes exactly the table in ARCHITECTURE.md §1.2 (ARAIL_INSTANCE,
# ARAIL_ENV_FILE, LAB_ROOT, ARAIL_DATA_DIR, LAB_PKB, ARAIL_EXPERIMENTS_DIR,
# ARAIL_MODELS_DIR, ARAIL_WORLDS_DIR, PORTAL_PORT, LANCE_PORT, BIND_ADDR,
# LAB_NAME, LAB_SHORT_NAME, LAB_THEME, LAB_INTENT). Never pass LAB_MODE,
# ARAIL_AUTOCHECKS, or any secret/token — the pack format has no field for
# them by design (§1.2 "Not in the pack").
inst_write_env_pack() {
    local slug="$1"; shift
    inst_load_env_writer || {
        echo "inst_write_env_pack: could not load _set_env_var from scripts/setup.sh" >&2
        return 1
    }
    local env_file
    env_file="$(inst_env_file "$slug")"
    mkdir -p "$(dirname "$env_file")"
    : > "$env_file"
    while [[ $# -gt 0 ]]; do
        [[ $# -ge 2 ]] || {
            echo "inst_write_env_pack: odd number of KEY VALUE arguments" >&2
            return 1
        }
        _set_env_var "$1" "$2" "$env_file"
        shift 2
    done
    chmod 0644 "$env_file"
}

#!/usr/bin/env bash
set -euo pipefail

# `pwd -P` (physical, symlinks resolved), not plain `pwd` (logical) — must
# match Python's Path.cwd()/os.getcwd(), which the OS always returns
# physical (REVIEW.md m5). A checkout reached through a symlinked
# directory component would otherwise make the readiness probe's checkout
# comparison (§2.3 step 4 / M1 below) fail forever.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$REPO_ROOT"

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/lib/instances.sh"
# Root-lab per-service readiness probing (ARCHITECTURE.md
# sprints/2026-07-29-elite-cli §8.1) — guarded per the same source
# discipline as everything else in this file (A2/F4): this script is
# copied wholesale into throwaway test repos, so a missing sibling file
# must degrade, never abort a non-interactive shell.
# shellcheck disable=SC1091
[[ -f "$REPO_ROOT/scripts/lib/services.sh" ]] && source "$REPO_ROOT/scripts/lib/services.sh"

# macOS Sequoia emits a harmless but noisy line from libsystem_malloc
# whenever a child Python process spawns: "Python(PID) MallocStackLogging:
# can't turn off malloc stack logging because it was not enabled." Filter
# it from stderr so it doesn't drown real log output.
if [[ "$(uname -s)" == "Darwin" ]]; then
    exec 2> >(grep -v 'MallocStackLogging: ' >&2)
fi

# ── ANSI color gating (ARCHITECTURE.md §13 "ANSI leaks into non-tty
# output", F25) — see arailctl's identical block for the full rationale
# (inlined per-script, not a shared lib/tty.sh, per A2). $'...' (ANSI-C
# quoting), not "...", so the variables hold real ESC bytes rather than
# the literal 4-char sequence `echo -e`/`printf` would otherwise need to
# reinterpret (arailctl's block has the full story).
if [[ -t 1 && "${ARAIL_COLOR:-auto}" != "never" && -z "${NO_COLOR:-}" ]] || [[ "${ARAIL_COLOR:-auto}" == "always" ]]; then
    GREEN=$'\033[0;32m'; CYAN=$'\033[0;36m'; BOLD=$'\033[1m'; RESET=$'\033[0m'; YELLOW=$'\033[0;33m'
else
    GREEN=""; CYAN=""; BOLD=""; RESET=""; YELLOW=""
fi

# Load .env first (for LAB_NAME and friends) before anything else.
# shellcheck disable=SC1091
[[ -f .env ]] && set -a && source .env && set +a
# lab.conf holds the ports setup.sh picked. Without `set -a` here, PORTAL_PORT
# reaches uvicorn's argv (shell-local expansion) but is never exported, so a
# child python process reading os.getenv("PORTAL_PORT") falls back to .env/
# default 8080 — the same drift arailctl's launchd branch already avoids.
# ARCHITECTURE.md §6.2 — fix is in scope for this WP (restructuring this file
# anyway; leaving a known drift bug in it is not defensible).
# Guard with [[ -f ]] rather than relying on `|| true`: under bash 3.2
# (macOS's shipped /bin/bash — confirmed while adding the B2 regression
# driver test in this checkout, which has no lab.conf), a "file not found"
# `source` error aborts a non-interactive shell outright even under a
# trailing `|| true` — the same landmine status.sh:27 was already fixed
# for (ARCHITECTURE.md §10's "Ruling on the two latent fixes"), left
# un-guarded here.
# shellcheck disable=SC1091
[[ -f lab.conf ]] && set -a && source lab.conf && set +a

LAB_NAME="${LAB_NAME:-Arail}"
LAB_SHORT_NAME="${LAB_SHORT_NAME:-arail}"
LAB_LOGO="${LAB_LOGO:-⟨${LAB_NAME}⟩}"

info() { echo -e "${GREEN}[${LAB_SHORT_NAME}]${RESET} $*"; }
# `warn` was called (ttyd-present/tmux-absent path, below) but never defined —
# a command-not-found abort under `set -euo pipefail`. ARCHITECTURE.md §10
# names this IN SCOPE for this WP: two lines, fixed here.
warn() { echo -e "${YELLOW}[${LAB_SHORT_NAME}]${RESET} $*"; }

# Shared by both Ollama start blocks below (the --world instance path and
# the legacy root-lab path). setup.sh skips the model pull/create when
# Ollama wasn't reachable at setup time (its own "daemon not reachable —
# skipping model download" warning). If that happened, the daemon coming
# up here isn't enough — chat's default model literally was never
# installed, which fails a second, more confusing way even once the
# daemon answers.
_check_default_ollama_model() {
    # Read the ACTUAL configured slot-A model (model_defaults.yaml, else
    # .env's MODEL_NAME, else the llama-ai-eng builtin) rather than
    # hardcoding llama-ai-eng — a lab settled onto a different primary
    # model used to get either a false "not installed" warning about a
    # model it doesn't even use, or silence about the one it does.
    # python3 here resolves through the venv activated above (line 233).
    local model
    model="$(python3 -m arail.model_defaults --get default_a 2>/dev/null)"
    [[ -n "$model" ]] || model="llama-ai-eng"
    if ! ollama list 2>/dev/null | awk 'NR>1{print $1}' | cut -d: -f1 | grep -qx "${model%%:*}"; then
        warn "Ollama     → model '${model}' isn't installed yet — chat's default model won't"
        warn "             work until you run:"
        if [[ "$model" == "llama-ai-eng" ]]; then
            warn "             ollama pull llama3.2:1b && ollama create ${model} -f models/ai-eng/Modelfile.default"
        else
            warn "             ollama pull ${model}"
        fi
    fi
}

export PATH="$HOME/.local/bin:$PATH"
BIND="${BIND_ADDR:-127.0.0.1}"
LANCE_PORT="${LANCE_PORT:-7414}"

# =============================================================================
#  Concurrent Worlds — argument parsing, picker, instance launch
#  (sprints/2026-07-28-concurrent-worlds/ARCHITECTURE.md §3)
# =============================================================================

WORLD_SLUG=""
PORT_OVERRIDE=""
LIST_ONLY=0
ASSUME_YES=0
# ARCHITECTURE.md §10 (sprints/2026-07-29-elite-cli, Ruling 5 — "non-
# interactive root start"): the only spelling for "start the root lab",
# even with |W|>=1 configured. Deliberately its own flag, never
# `--world root` — a World literally named "root" must stay startable
# (F11), so the two must never collapse into one code path.
ROOT_ONLY=0
# ARCHITECTURE.md §11 (sprints/2026-07-29-elite-cli, Ruling 6 — "warm-up"):
# an explicit, honest opt-in to the warmer that already exists in-process
# (app.py:_warm_primary_router) — never a second warm path. Applies to
# BOTH the instance path and the root path below.
WARM_UP=0
# Force the interactive picker even when |W| < 2 (which would otherwise
# auto-select the single World, or skip straight to the root lab). This is
# what `./arailctl switch` uses so it never has to carry a second copy of
# the picker — the one at the bottom of this section stays the only one.
PICK_ONLY=0

_start_usage() {
    echo "Usage: ./arailctl start [--world <slug>] [--root] [--port <n>] [--no-browser] [--list] [--pick] [--yes] [--warm]"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --world)
            [[ $# -ge 2 ]] || { echo "--world requires a slug" >&2; _start_usage >&2; exit 2; }
            WORLD_SLUG="$2"; shift 2 ;;
        --world=*) WORLD_SLUG="${1#--world=}"; shift ;;
        --root) ROOT_ONLY=1; shift ;;
        --port)
            [[ $# -ge 2 ]] || { echo "--port requires a number" >&2; _start_usage >&2; exit 2; }
            PORT_OVERRIDE="$2"; shift 2 ;;
        --port=*) PORT_OVERRIDE="${1#--port=}"; shift ;;
        --no-browser) ARAIL_NO_BROWSER=1; shift ;;
        --list) LIST_ONLY=1; shift ;;
        --pick) PICK_ONLY=1; shift ;;
        --yes) ASSUME_YES=1; shift ;;
        --warm) WARM_UP=1; shift ;;
        -h|--help) _start_usage; exit 0 ;;
        *)
            echo "Unknown flag: $1" >&2
            _start_usage >&2
            exit 2 ;;
    esac
done
export ARAIL_NO_BROWSER="${ARAIL_NO_BROWSER:-0}"

# ARCHITECTURE.md §10: --root and --world are mutually exclusive by
# construction — checked immediately after parsing, before anything else
# (including the daemon guard) touches either flag, so a usage mistake is
# always reported as one regardless of daemon/World state.
if [[ "$ROOT_ONLY" == "1" && -n "$WORLD_SLUG" ]]; then
    echo "--root and --world are mutually exclusive" >&2
    _start_usage >&2
    exit 2
fi

# --pick means "ask me"; --root/--world mean "don't". Naming both is a
# usage mistake, reported as one here rather than silently letting the
# explicit target win (which would make `switch --root --pick` look like
# it prompted when it never did). Same shape as the check above.
if [[ "$PICK_ONLY" == "1" ]] && { [[ "$ROOT_ONLY" == "1" ]] || [[ -n "$WORLD_SLUG" ]]; }; then
    echo "--pick cannot be combined with --root or --world" >&2
    _start_usage >&2
    exit 2
fi
# --pick says "ask me", --yes says "never ask". Naming both is a usage
# mistake in the other direction, and reported as one for the same reason.
if [[ "$PICK_ONLY" == "1" && "$ASSUME_YES" == "1" ]]; then
    echo "--pick and --yes are mutually exclusive (--pick asks, --yes does not)" >&2
    _start_usage >&2
    exit 2
fi

# QA-2: `--port` used to accept anything matching ^[0-9]+$ — including 0
# (privileged/ephemeral-request port; uvicorn then binds a real ephemeral
# port the readiness probe can never reach) and values above the 65535 TCP
# ceiling (the bind check at stage [5/8] passes vacuously, since
# _port_in_use can never find a listener on an impossible port). Reject
# both here, before any World is even resolved.
if [[ -n "$PORT_OVERRIDE" ]]; then
    if ! [[ "$PORT_OVERRIDE" =~ ^[0-9]+$ ]] \
        || (( 10#$PORT_OVERRIDE < 1 || 10#$PORT_OVERRIDE > 65535 )); then
        echo "--port ${PORT_OVERRIDE} is not a valid port (must be 1-65535)" >&2
        exit 2
    fi
fi

# Daemon mode guard: when launchd supervises the lab, a foreground start
# would fight the agents over the ports. Use the supervised commands.
# daemon_active() (scripts/lib/instances.sh) requires BOTH the plist file
# AND a live launchctl PID — a plist that exists but isn't loaded (e.g.
# after `./arailctl stop`, which unloads but keeps plists) no longer trips
# this guard, retiring the plist-existence trap (F9).
#
# Runs AFTER argument parsing (REVIEW.md m2 — was before it): `--list` is
# side-effect-free and must work regardless of daemon state; `--help`
# already exited during parsing above. `--world` names the slug in the
# refusal (ARCHITECTURE.md §4.4) instead of a generic message — this is now
# the ONLY place this check runs; `_instance_start`'s stage [1/8] no longer
# duplicates it, since this guard has already refused before that stage
# could ever be reached (REVIEW.md m2: "delete the duplicate").
if [[ "$LIST_ONLY" != "1" ]]; then
    if daemon_active; then
        if [[ -n "$WORLD_SLUG" ]]; then
            echo "Daemon mode is active (launchd supervises the lab on :${PORTAL_PORT:-8080})."
            echo "Daemon mode is single-instance: it cannot host a second World."
            echo "  To run Worlds side by side:  ./arailctl uninstall-daemon && ./arailctl start --world ${WORLD_SLUG}"
            echo "  To keep the daemon:          use the lab it already serves at http://${BIND:-127.0.0.1}:${PORTAL_PORT:-8080}"
        elif [[ "$ROOT_ONLY" == "1" ]]; then
            # ARCHITECTURE.md §4.2/§8.3: symmetric with the --world refusal
            # above — launchd already runs the root lab, so --root here is
            # redundant rather than impossible, but it is still refused
            # (was: silently ignored, falling through to a kickstart one
            # level up in arailctl) so the operator's explicit ask is never
            # silently discarded.
            echo "Daemon mode is active (launchd supervises the lab on :${PORTAL_PORT:-8080})."
            echo "--root is redundant here — launchd already runs the root lab."
            echo "  To use it now:    http://${BIND:-127.0.0.1}:${PORTAL_PORT:-8080}"
            echo "  To restart it:    ./arailctl restart --root"
            echo "  Dev mode:         ./arailctl uninstall-daemon && ./arailctl start --root"
        else
            echo "Daemon mode is active (launchd supervises the lab)."
            echo "  Restart:  ./arailctl restart"
            echo "  Dev mode: ./arailctl uninstall-daemon && ./arailctl start"
        fi
        exit 1
    elif daemon_plist_installed; then
        echo "launchd plists installed but inactive — starting in the foreground."
    fi
fi

[[ -f .venv/bin/activate ]] || { echo "no .venv — run ./arailctl setup"; exit 1; }
# shellcheck disable=SC1091
source .venv/bin/activate

# Absolute, shared roots — computed once here (from the ROOT .env, which may
# set a relative override) so every instance pack pins the SAME weights/
# Worlds directories regardless of the instance's own LAB_ROOT.
_instance_abs_path() {
    local p="${1:-}"
    case "$p" in
        /*) printf '%s' "$p" ;;
        "") printf '%s' "$REPO_ROOT" ;;
        *)  printf '%s/%s' "$REPO_ROOT" "$p" ;;
    esac
}
_INST_MODELS_DIR_ABS="$(_instance_abs_path "${ARAIL_MODELS_DIR:-lab/models}")"
_INST_WORLDS_DIR_ABS="$(_instance_abs_path "${ARAIL_WORLDS_DIR:-lab/worlds}")"

# ── World catalog (only Worlds THIS lab can see; never raises) ─────────────
_instance_world_catalog() {
    ARAIL_WORLDS_DIR="$_INST_WORLDS_DIR_ABS" python3 - <<'PY'
import json
from arail.world_mount import list_available_worlds
worlds = [w for w in list_available_worlds() if w.valid]
print(json.dumps([{"slug": w.slug, "display_name": w.display_name} for w in worlds]))
PY
}

# The root lab is not always "the plain AI Lab": a World can be MOUNTED
# into it (lab/data/world-mount.json), in which case option 0 of the
# picker boots that World's lab, while the same World also appears as its
# own row further down — two rows, one lab, indistinguishable. Read the
# mount so option 0 can name what it will actually give you.
#
# Same never-raises posture as _instance_world_catalog: any failure prints
# nothing and the caller falls back to the plain label.
_instance_root_mounted_name() {
    python3 - 2>/dev/null <<'PY' || true
import json
from pathlib import Path

try:
    from arail.world_mount import current_mount

    rec = current_mount()
    if rec is not None:
        # The mount record is the authority for WHICH World; manifest.json
        # only supplies the pretty name. Read it directly rather than via
        # load_bundle() — a full 6-sibling parse (and its PartialBundle
        # raise) is far more than a label needs, and a bundle that has
        # since gone missing must still print something useful.
        name = rec.world
        try:
            manifest = json.loads(
                (Path(rec.bundle_dir) / "manifest.json").read_bytes()
            )
            name = str(manifest.get("display_name") or "").strip() or rec.world
        except Exception:
            pass
        print(name)
except Exception:
    pass
PY
}

_instance_print_known_slugs() {
    printf '%s' "$WORLD_CATALOG_JSON" | python3 -c '
import json, sys
worlds = json.load(sys.stdin)
if not worlds:
    print("  (no Worlds configured — see ./arailctl world list)")
else:
    for w in worlds:
        print("  " + w["slug"])
'
}

_instance_print_roster() {
    local slug live rec port
    while IFS= read -r slug; do
        [[ -n "$slug" ]] || continue
        if inst_alive "$slug"; then
            rec="$(inst_read_record "$slug")"
            port="$(inst_record_field "$rec" portal_port)"
            live="running :${port}"
        else
            live="not running"
        fi
        echo "  ${slug}  (${live})  — ./arailctl stop --world ${slug}"
    done < <(inst_list_slugs)
}

# F11: a World bundle can be validly named "root" (INST_SLUG_RE has no
# reserved words) — that is NOT the same thing as --root, which always
# means the root lab. True iff such a bundle is in the catalog, so the
# picker (both its interactive and non-interactive forms) can spell out
# the disambiguation rather than let the two silently look alike.
_instance_world_named_root_exists() {
    printf '%s' "$WORLD_CATALOG_JSON" | python3 -c '
import json, sys
worlds = json.load(sys.stdin)
sys.exit(0 if any(w["slug"] == "root" for w in worlds) else 1)
'
}

WORLD_CATALOG_JSON="$(_instance_world_catalog)"
WORLD_COUNT="$(printf '%s' "$WORLD_CATALOG_JSON" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')"

if [[ "$LIST_ONLY" == "1" ]]; then
    printf '%s' "$WORLD_CATALOG_JSON" | python3 -c '
import json, sys
worlds = json.load(sys.stdin)
if not worlds:
    print("No Worlds available — this lab runs the root AI Lab only.")
else:
    for w in worlds:
        print(w["slug"].ljust(20) + " " + w["display_name"])
'
    exit 0
fi

# ── The picker's memory (Phase 1) ──────────────────────────────────────────
# Resolve "what did I launch last" ONCE, here, so both the interactive
# picker and the --yes path agree on the default. Two outputs:
#   _LAST_KIND  — "root" | "world" | ""   ("" = no usable memory)
#   _LAST_SLUG  — the World slug when _LAST_KIND is "world"
# A remembered World that has since been deleted from the catalog degrades
# to the root lab with one honest line — never a hard failure, and never a
# silent substitution.
_LAST_KIND="" _LAST_SLUG="" _LAST_GONE=""
if _last_raw="$(inst_read_last_target 2>/dev/null)"; then
    if [[ "$_last_raw" == root ]]; then
        _LAST_KIND="root"
    else
        _LAST_SLUG="${_last_raw#*$'\t'}"
        # Re-check against THIS lab's catalog: the memory is a preference,
        # the catalog is the truth. A World deleted (or made invalid) since
        # it was last launched must not become an unstartable default.
        if printf '%s' "$WORLD_CATALOG_JSON" | python3 -c '
import json, sys
sys.exit(0 if any(w["slug"] == sys.argv[1] for w in json.load(sys.stdin)) else 1)
' "$_LAST_SLUG"; then
            _LAST_KIND="world"
        else
            _LAST_GONE="$_LAST_SLUG"
            _LAST_KIND="root"
            _LAST_SLUG=""
        fi
    fi
fi
unset _last_raw

# _last_default_label — how the resolved memory reads in a prompt/message.
_last_default_label() {
    if [[ "$_LAST_KIND" == "world" ]]; then
        printf '%s' "$_LAST_SLUG"
    else
        printf '%s' "the root lab"
    fi
}

# ── Resolve which slug (if any) we are starting as an instance ─────────────
TARGET_SLUG=""
if [[ "$ROOT_ONLY" == "1" ]]; then
    # ARCHITECTURE.md §10: checked FIRST, ahead of --world/|W| counting/the
    # picker — --root short-circuits all of it straight to the legacy root
    # path below. Mutual exclusion with --world was already enforced right
    # after parsing, so WORLD_SLUG is guaranteed empty by this point.
    TARGET_SLUG=""
elif [[ -n "$WORLD_SLUG" ]]; then
    TARGET_SLUG="$WORLD_SLUG"
elif [[ "$WORLD_COUNT" == "0" ]]; then
    # Legacy root lab — falls straight into the unchanged path below. Even
    # --pick has nothing to offer here: a picker whose only option is
    # option 0 is a prompt, not a choice.
    TARGET_SLUG=""
elif [[ "$WORLD_COUNT" == "1" && "$PICK_ONLY" != "1" ]]; then
    # VISION §3: "the picker must not tax the single-World user, who is
    # most of the fork audience." Still true — but once there IS a memory,
    # honouring it is not a tax, it is the whole point of the memory. A
    # single-World lab whose operator last ran the root lab must come back
    # to the root lab. `switch`/`--pick` opts into the prompt explicitly.
    if [[ "$_LAST_KIND" == "root" ]]; then
        TARGET_SLUG=""
        echo "Starting the root lab (last used). Override: --world <slug>, or --pick to choose."
    else
        TARGET_SLUG="$(printf '%s' "$WORLD_CATALOG_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["slug"])')"
    fi
else
    if [[ "$ASSUME_YES" == "1" ]]; then
        # Checked BEFORE the tty test, not after: --yes is the explicit
        # "take the default" opt-in, and its whole audience is scripts and
        # pipes — a non-tty check that ran first would make the flag
        # unreachable in exactly the situation it exists for.
        #
        # docs/cli.md has always documented --yes as "non-interactive
        # default for the World picker"; before the memory existed there
        # was no default to take, so it fell in with the refusal below and
        # exited 2 — the doc and the code disagreed. Now it takes the
        # remembered target, and says which one out loud.
        [[ -n "$_LAST_GONE" ]] && echo "Last-used World '${_LAST_GONE}' is no longer in this lab's catalog — using the root lab."
        if [[ "$_LAST_KIND" == "world" ]]; then
            TARGET_SLUG="$_LAST_SLUG"
            echo "--yes: starting '${_LAST_SLUG}' (last used)."
        else
            TARGET_SLUG=""
            echo "--yes: starting the root lab$([[ "$_LAST_KIND" == "root" ]] && printf ' (last used)')."
        fi
    elif [[ ! -t 0 ]]; then
        # Bare non-interactive: unchanged. VISION §3's "never guess" ruling
        # is what CI, daemons, and every scripted caller depend on — the
        # memory above is an OPT-IN (--yes), never something a pipe inherits.
        echo "Multiple Worlds are configured — pick one:" >&2
        printf '%s' "$WORLD_CATALOG_JSON" | python3 -c '
import json, sys
for w in json.load(sys.stdin):
    print("  ./arailctl start --world " + w["slug"], file=sys.stderr)
'
        # ARCHITECTURE.md §10: the non-interactive refusal must teach
        # --root alongside the per-World lines — this is the entire fix
        # for "CI/daemons cannot start the root lab" (gap 2).
        echo "  ./arailctl start --root       (the root lab, not a World)" >&2
        if _instance_world_named_root_exists; then
            echo "  NOTE: a World is also named 'root' — --root always means the root lab; use --world root for that World (F11)." >&2
        fi
        if [[ "$PICK_ONLY" == "1" ]]; then
            echo "  (--pick needs a terminal — stdin is not a tty)" >&2
        fi
        exit 2
    else
    echo ""
    echo "Which lab do you want?"
    echo ""
    [[ -n "$_LAST_GONE" ]] && echo "  (last-used World '${_LAST_GONE}' is no longer in this lab's catalog)"
    # Option 0 must name what it will ACTUALLY start. A World mounted into
    # the root lab makes "the root lab" and that World's own row two names
    # for two different labs that look identical here.
    _root_mounted_name="$(_instance_root_mounted_name)"
    if [[ -n "$_root_mounted_name" ]]; then
        _root_label="${LAB_NAME} — ${_root_mounted_name} mounted (:${PORTAL_PORT:-8080})"
    else
        _root_label="${LAB_NAME} (the root lab on :${PORTAL_PORT:-8080})"
    fi
    printf '  0) %s%s\n' "$_root_label" "$([[ "$_LAST_KIND" == "root" ]] && printf '   ← last')"
    if _instance_world_named_root_exists; then
        echo "     NOTE: a World is also named 'root' below — that is a different thing from option 0 (F11)."
    fi
    _rows="$(printf '%s' "$WORLD_CATALOG_JSON" | python3 -c '
import json, sys
for w in json.load(sys.stdin):
    print(w["slug"] + "\t" + w["display_name"])
')"
    _slugs=()
    _i=1
    while IFS=$'\t' read -r _row_slug _row_name; do
        [[ -n "$_row_slug" ]] || continue
        if inst_alive "$_row_slug"; then
            _rec="$(inst_read_record "$_row_slug")"
            _port="$(inst_record_field "$_rec" portal_port)"
            _live="● running :${_port}"
        else
            _live="○ not running"
        fi
        _mark=""
        [[ "$_LAST_KIND" == "world" && "$_row_slug" == "$_LAST_SLUG" ]] && _mark="   ← last"
        printf '  %d) %-22s %s%s\n' "$_i" "$_row_name" "$_live" "$_mark"
        _slugs+=("$_row_slug")
        _i=$((_i + 1))
    done <<< "$_rows"

    # The Enter-default: the remembered target, or option 0 on a lab that
    # has never launched anything. Spelled out in the prompt so pressing
    # Enter is never a guess about what you'll get.
    _default_idx=0
    if [[ "$_LAST_KIND" == "world" ]]; then
        _n=1
        for _s in "${_slugs[@]}"; do
            [[ "$_s" == "$_LAST_SLUG" ]] && _default_idx="$_n"
            _n=$((_n + 1))
        done
        unset _n _s
    fi
    echo ""
    # printf to STDOUT + a bare `read -r`, never `read -rp`. On Darwin this
    # script replaces fd 2 with a pipe into `grep -v MallocStackLogging`
    # (see the top of this file) — and `read -p` writes its prompt to
    # STDERR with no trailing newline, which `grep` then holds in its
    # buffer indefinitely, because an incomplete final line is never
    # emitted until EOF. Net effect on every Mac: the World list rendered,
    # then a blank line and a cursor, with the "Choice […]:" prompt never
    # appearing at all — the picker looked hung rather than interactive.
    printf '  Choice [0-%d, Enter = %d]: ' "$((_i - 1))" "$_default_idx"
    read -r _choice
    [[ -z "$_choice" ]] && _choice="$_default_idx"
    if [[ "$_choice" == "0" ]]; then
        TARGET_SLUG=""
    elif [[ "$_choice" =~ ^[0-9]+$ ]] && (( _choice >= 1 && _choice < _i )); then
        TARGET_SLUG="${_slugs[$((_choice - 1))]}"
    else
        echo "Invalid choice." >&2
        exit 2
    fi
    fi
fi

# ── Instance-mode support functions ─────────────────────────────────────────

_INST_PIDS=()
_INST_CLAIM_FILE=""
# REVIEW.md M3: Ollama is machine-SHARED (§11 "Nothing cross-instance",
# §4.2 step 5) — it must NEVER be a member of _INST_PIDS, or this
# launcher's own Ctrl-C/TERM cleanup would kill it out from under a
# sibling instance that is still using it, regardless of whether that
# sibling is alive. Tracked separately; killed here only if no OTHER
# World instance is still alive (same "last one out" guard reset.sh's
# stop_instance() already applies on the explicit-stop path).
_INST_OLLAMA_PID=""
_INST_CURRENT_SLUG=""

_instance_cleanup_and_exit() {
    # Disarm the EXIT trap FIRST — this function itself calls `exit` below,
    # and a still-armed EXIT trap would otherwise fire a second time as
    # that exit unwinds the shell (harmless since every step here is
    # idempotent, but needless and potentially confusing in logs).
    trap - EXIT INT TERM
    local code="${1:-0}"
    local pid
    for pid in "${_INST_PIDS[@]:-}"; do
        [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
    done
    if [[ ${#_INST_PIDS[@]} -gt 0 ]]; then
        wait 2>/dev/null || true
    fi
    if [[ -n "$_INST_OLLAMA_PID" ]]; then
        local other_alive=0 other_slug
        while IFS= read -r other_slug; do
            [[ -n "$other_slug" && "$other_slug" != "$_INST_CURRENT_SLUG" ]] || continue
            inst_alive "$other_slug" && other_alive=1
        done < <(inst_list_slugs)
        if (( other_alive == 0 )); then
            kill "$_INST_OLLAMA_PID" 2>/dev/null || true
            wait "$_INST_OLLAMA_PID" 2>/dev/null || true
            rm -f "$(inst_data_dir "$_INST_CURRENT_SLUG")/.ollama-started-by-arail.pid" 2>/dev/null || true
        fi
    fi
    [[ -n "$_INST_CLAIM_FILE" ]] && rm -f "$_INST_CLAIM_FILE" 2>/dev/null || true
    exit "$code"
}

# Resolve + jail + seal-verify a World slug. Prints a JSON object; never
# raises. ARCHITECTURE.md §3.5 stage 1 / F5.
_instance_resolve_world() {
    local slug="$1"
    ARAIL_WORLDS_DIR="$_INST_WORLDS_DIR_ABS" python3 - "$slug" <<'PY'
import json, sys
from arail.world_mount import _SLUG_RE, _default_worlds_dir, load_bundle, verify_seal

slug = sys.argv[1]

def fail(reason):
    print(json.dumps({"ok": False, "reason": reason}))
    sys.exit(0)

if not _SLUG_RE.match(slug):
    fail("invalid World slug: " + slug)

worlds_root = _default_worlds_dir().resolve()
candidate = (worlds_root / slug).resolve()
root_s, cand_s = str(worlds_root), str(candidate)
if not (cand_s == root_s or cand_s.startswith(root_s + "/")):
    fail("path escapes the Worlds directory")
if not candidate.is_dir():
    fail("no such World: " + slug)

try:
    bundle = load_bundle(candidate)
except Exception as e:  # noqa: BLE001
    fail(str(e))

seal = verify_seal(bundle)
if not seal.ok:
    fail(seal.user_message)

theme = ""
try:
    if bundle.face:
        theme = str(bundle.face.get("theme", {}).get("personality", ""))
except Exception:  # noqa: BLE001
    pass

print(json.dumps({
    "ok": True,
    "bundle_dir": str(candidate),
    "display_name": str(bundle.manifest.get("display_name", slug)),
    "theme": theme,
}))
PY
}

# _json_field <json-or-garbage> <field> — never raises, never aborts the
# caller under `set -euo pipefail`. QA-8 / REVIEW.md n2: the probe at stage
# [6/8] feeds this whatever answered the port with HTTP 200 — which, once
# QA-B1 is fixed and the probe actually receives real bodies, can be
# anything (a foreign web app's HTML, a JSON array/scalar). A bare
# json.loads()/`.get()` with no try/except turned that into a raw
# JSONDecodeError/AttributeError that aborted the stage instead of letting
# M1's named "token/checkout mismatch" error fire. Prints "" (not an
# error) on anything that isn't a JSON object — mirrors
# inst_record_field's "empty string, not an error" contract.
_json_field() {
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

# _warm_report <api-instance-url> — ARCHITECTURE.md §11.1 (sprints/2026-07-
# 29-elite-cli, Ruling 6 — "warm-up"). Polls GET <url> (the portal's OWN
# /api/instance — no new endpoint) every 0.5s up to ARAIL_WARM_TIMEOUT_SEC
# (default 90) for "warm": true, then prints exactly ONE honest line. Shared
# by both the instance path and the root path below (both pass
# ARAIL_TIER0_BOOT_WARM=1 to their own portal invocation when --warm was
# given, then call this against their own /api/instance).
#
# F14: never gates readiness, never touches the exit code — every path
# through this function `return 0`s. Called strictly AFTER the caller has
# already declared the lab up (after the record + URL banner), never before.
_warm_report() {
    local url="$1"
    local timeout_sec="${ARAIL_WARM_TIMEOUT_SEC:-90}"
    [[ "$timeout_sec" =~ ^[0-9]+$ ]] && (( timeout_sec > 0 )) || timeout_sec=90
    if ! command -v curl >/dev/null 2>&1; then
        echo "warm-up: — curl not found, cannot poll warm-up status"
        return 0
    fi
    local cap=$(( timeout_sec * 2 )) waited=0
    local body warm backend warm_ms warm_skipped secs
    while (( waited < cap )); do
        body="$(curl -sf -m 0.7 "$url" 2>/dev/null || true)"
        if [[ -n "$body" ]]; then
            warm="$(_json_field "$body" warm)"
            if [[ "$warm" == "True" ]]; then
                backend="$(_json_field "$body" backend)"
                warm_ms="$(_json_field "$body" warm_ms)"
                warm_skipped="$(_json_field "$body" warm_skipped)"
                if [[ -n "$warm_skipped" ]]; then
                    echo "warm-up: — not applicable for backend ${backend:-unknown} (weights load in-process; the portal warms itself on boot)"
                elif [[ -n "$warm_ms" ]]; then
                    secs="$(python3 -c 'import sys; print(f"{int(sys.argv[1]) / 1000:.1f}")' "$warm_ms" 2>/dev/null || echo '?')"
                    echo "warm-up: ✓ via ${backend:-the primary router} in ${secs}s"
                else
                    echo "warm-up: ✓ ready (via ${backend:-the primary router})"
                fi
                return 0
            fi
        fi
        sleep 0.5
        waited=$((waited + 1))
    done
    echo "warm-up: ⚠ not complete within ${timeout_sec}s — the first chat message will be slower"
    return 0
}

# _instance_port_conflicts_with_other_slug <this-slug> <port> — true iff
# <port> (portal or lance) is already pinned in ANOTHER instance's registry
# record (live or not — a registered-but-not-live record still owns its
# ports, mirroring inst_ports_registered's own contract). Excludes <this-
# slug>'s own record so a re-boot re-pinning its OWN already-owned port
# is never treated as a collision with itself.
_instance_port_conflicts_with_other_slug() {
    local this_slug="$1" port="$2"
    local other_slug rec p
    while IFS= read -r other_slug; do
        [[ -n "$other_slug" && "$other_slug" != "$this_slug" ]] || continue
        rec="$(inst_read_record "$other_slug" 2>/dev/null)" || continue
        p="$(inst_record_field "$rec" portal_port)"
        [[ -n "$p" && "$p" == "$port" ]] && return 0
        p="$(inst_record_field "$rec" lance_port)"
        [[ -n "$p" && "$p" == "$port" ]] && return 0
    done < <(inst_list_slugs)
    return 1
}

# _instance_validate_port_override <slug> <portal> <lance> — QA-1/QA-2/QA-5:
# `--port` used to reach the pack unchecked on BOTH the first-boot and the
# re-boot branch, skipping every check inst_allocate_ports itself performs
# (the exclusion list, the registry-collision check). Route both branches
# through the same two checks here so a pinned override can never land on a
# reserved port or a port another World instance already owns. Prints the
# named reason on stderr and returns 1; the caller handles the "✗" + exit.
_instance_validate_port_override() {
    local slug="$1" portal="$2" lance="$3"
    if inst_port_excluded "$portal" || inst_port_excluded "$lance"; then
        echo "  --port ${portal} collides with a reserved port" >&2
        return 1
    fi
    if _instance_port_conflicts_with_other_slug "$slug" "$portal" \
        || _instance_port_conflicts_with_other_slug "$slug" "$lance"; then
        echo "  --port ${portal} collides with a port already registered to another World instance" >&2
        return 1
    fi
    return 0
}

# _instance_db_ensure <data_dir> — readiness-gate the relational store for
# exactly one data_dir, quietly if already ok, one line if it acted, one
# warning (never fatal) if it can't act. Shared by the World-instance path
# (_instance_start) and the root-lab boot path further down.
# sprints/2026-08-10-arail2-persistence-instantiated §4.5/§4.1.
_instance_db_ensure() {
    local data_dir="$1"
    [[ -n "$data_dir" ]] || return 0
    [[ -f "$REPO_ROOT/.venv/bin/activate" ]] || return 0
    local line rc=0
    line="$(cd "$REPO_ROOT" && source .venv/bin/activate && \
        python -m arail.dbspec.ensure "$data_dir" --apply --quiet-ok 2>&1)" || rc=$?
    [[ -n "$line" ]] && echo "  $line"
    return 0
}

# The 8-stage instance launch. ARCHITECTURE.md §3.5.
_instance_start() {
    local slug="$1"
    local display_name="$slug"
    _INST_CURRENT_SLUG="$slug"

    echo ""
    echo -e "${CYAN}${BOLD}Starting World: ${slug}${RESET}"
    echo ""

    # ── [1/8] Preflight ──────────────────────────────────────────────
    # The daemon-active check used to be duplicated here; it is now the
    # top-level guard's job (runs once, after arg parsing — REVIEW.md m2),
    # so by the time _instance_start is ever called, daemon_active is
    # already known false. Not re-checked here to avoid two sources of
    # truth for the same predicate.
    printf '[1/8] Preflight… '
    if [[ ! -f .venv/bin/activate ]]; then
        echo "✗"; echo "  no .venv — run ./arailctl setup" >&2; exit 1
    fi
    if ! inst_alive "$slug"; then
        local ceiling live_n
        ceiling="${LAB_MAX_INSTANCES:-$INST_MAX_INSTANCES_DEFAULT}"
        live_n=0
        while IFS= read -r s; do
            [[ -n "$s" ]] && inst_alive "$s" && live_n=$((live_n + 1))
        done < <(inst_list_slugs)
        if (( live_n >= ceiling )) && (( ceiling < 4 )); then
            echo "✗"
            echo "  At the instance ceiling (${ceiling}). Running Worlds:" >&2
            _instance_print_roster >&2
            echo "  Stop one first, or raise LAB_MAX_INSTANCES." >&2
            exit 1
        elif (( live_n >= ceiling )); then
            warn "over the default instance ceiling (${ceiling}) — LAB_MAX_INSTANCES raised; proceeding."
        fi
    fi
    echo "✓"

    # Attach-on-running — never respawn, never error (VISION §3, §3.3).
    if inst_alive "$slug" --probe; then
        local rec port started pid dn url
        rec="$(inst_read_record "$slug")"
        port="$(_json_field "$rec" portal_port)"
        started="$(_json_field "$rec" started_at)"
        pid="$(_json_field "$rec" portal_pid)"
        dn="$(_json_field "$rec" display_name)"
        url="http://${BIND}:${port}"
        echo ""
        echo "${dn:-$slug} is already running."
        echo "  URL:        ${url}"
        echo "  Data root:  $(inst_instance_dir "$slug")"
        echo "  Started:    ${started} (pid ${pid})"
        # Attaching IS launching, as far as the operator is concerned —
        # record it so the picker's default follows where they actually
        # went, not only where a process happened to be spawned.
        inst_write_last_target world "$slug"
        if [[ "${ARAIL_NO_BROWSER:-0}" != "1" ]] && [[ -t 1 ]]; then
            echo "Opening in your browser… (suppress with ARAIL_NO_BROWSER=1)"
            if command -v open >/dev/null; then open "$url"
            elif command -v xdg-open >/dev/null; then xdg-open "$url" >/dev/null 2>&1
            fi
        fi
        exit 0
    fi

    # ── [2/8] Resolve World ──────────────────────────────────────────
    printf '[2/8] Resolve World… '
    local resolve_json resolve_ok reason bundle_dir theme
    resolve_json="$(_instance_resolve_world "$slug")"
    resolve_ok="$(_json_field "$resolve_json" ok)"
    if [[ "$resolve_ok" != "True" ]]; then
        echo "✗"
        reason="$(_json_field "$resolve_json" reason)"
        echo "  ${reason}" >&2
        echo "  Known Worlds:" >&2
        _instance_print_known_slugs >&2
        exit 2
    fi
    bundle_dir="$(_json_field "$resolve_json" bundle_dir)"
    display_name="$(_json_field "$resolve_json" display_name)"
    theme="$(_json_field "$resolve_json" theme)"
    echo "✓"

    # ── [3/8] Claim ──────────────────────────────────────────────────
    printf '[3/8] Claim… '
    local reg_dir
    reg_dir="$(inst_registry_dir)"
    mkdir -p "$reg_dir" 2>/dev/null || true
    # QA-3: `( set -o noclobber; echo > file )` fails identically for
    # EEXIST (a real concurrent start) and EACCES (an unwritable
    # registry.d) — the claim branch below then reports "another start …
    # (pid ?)" for a plain permissions problem. Distinguish the two BEFORE
    # attempting the claim, so a permissions issue is named as one.
    if [[ ! -w "$reg_dir" ]]; then
        echo "✗"
        echo "  ${reg_dir} is not writable — check permissions (e.g. chmod u+w ${reg_dir})" >&2
        exit 1
    fi
    inst_prune "$slug"
    local claim_file
    claim_file="$(inst_claim_file "$slug")"
    if [[ -f "$claim_file" ]]; then
        local claim_mtime claim_age
        claim_mtime="$(stat -f %m "$claim_file" 2>/dev/null || stat -c %Y "$claim_file" 2>/dev/null || echo 0)"
        claim_age=$(( $(date +%s) - claim_mtime ))
        if (( claim_age > INST_CLAIM_STALE_SECONDS )); then
            rm -f "$claim_file"
        fi
    fi
    if ( set -o noclobber; echo "$$" > "$claim_file" ) 2>/dev/null; then
        _INST_CLAIM_FILE="$claim_file"
        trap '_instance_cleanup_and_exit 130' INT
        trap '_instance_cleanup_and_exit 143' TERM
        # REVIEW.md M4: only INT/TERM were trapped. Every EXPLICIT failure
        # path in this function already calls _instance_cleanup_and_exit,
        # but an IMPLICIT `set -euo pipefail` abort (a failing
        # inst_write_env_pack, a failing `source "$pack_file"`, a failing
        # python3 record serialiser, SIGHUP from a closed terminal, …)
        # would skip straight past all of them and leak the claim for the
        # full 120s stale window — F6's text is explicit: "removes the
        # claim on EVERY exit path." An EXIT trap catches all of those;
        # cleared just before the final `wait` below, once the launch has
        # actually succeeded and INT/TERM take over as the only exit paths.
        trap '_instance_cleanup_and_exit $?' EXIT
    else
        echo "✗"
        local holder
        holder="$(cat "$claim_file" 2>/dev/null || echo '?')"
        echo "  another start for '${slug}' is in progress (pid ${holder})" >&2
        exit 1
    fi
    echo "✓"

    # ── [4/8] Instance root ──────────────────────────────────────────
    printf '[4/8] Instance root… '
    local instance_root pack_file portal_port lance_port
    instance_root="$(inst_scaffold_instance_root "$slug")"
    pack_file="$(inst_env_file "$slug")"

    if [[ -f "$pack_file" ]]; then
        # Re-boot: read the pinned pack, assert absolute paths (§6.4 guard 1).
        local prior_lab_root
        prior_lab_root="$(grep -E '^LAB_ROOT=' "$pack_file" | head -n1 | cut -d= -f2- | tr -d '"')"
        case "$prior_lab_root" in
            /*) : ;;
            *)
                echo "✗"
                echo "  instance.env has a non-absolute LAB_ROOT — refusing to boot: ${pack_file}" >&2
                _instance_cleanup_and_exit 1 ;;
        esac
        portal_port="$(grep -E '^PORTAL_PORT=' "$pack_file" | head -n1 | cut -d= -f2- | tr -d '"')"
        lance_port="$(grep -E '^LANCE_PORT=' "$pack_file" | head -n1 | cut -d= -f2- | tr -d '"')"
        if [[ -n "$PORT_OVERRIDE" && "$PORT_OVERRIDE" != "$portal_port" ]]; then
            local reboot_portal reboot_lance
            reboot_portal="$PORT_OVERRIDE"
            reboot_lance=$(( reboot_portal + INST_PORT_LANCE_OFFSET - INST_PORT_PORTAL_OFFSET ))
            # QA-1: this branch (re-boot) used to skip inst_port_excluded
            # entirely — a --port that a FIRST boot correctly refuses was
            # silently pinned on the second invocation.
            if ! _instance_validate_port_override "$slug" "$reboot_portal" "$reboot_lance"; then
                echo "✗"
                _instance_cleanup_and_exit 1
            fi
            portal_port="$reboot_portal"
            lance_port="$reboot_lance"
            inst_write_env_pack "$slug" \
                ARAIL_INSTANCE "$slug" \
                ARAIL_ENV_FILE "$pack_file" \
                LAB_ROOT "$instance_root" \
                ARAIL_DATA_DIR "$instance_root/data" \
                LAB_PKB "$instance_root/pkb" \
                ARAIL_EXPERIMENTS_DIR "$instance_root/data/experiments" \
                ARAIL_MODELS_DIR "$_INST_MODELS_DIR_ABS" \
                ARAIL_WORLDS_DIR "$_INST_WORLDS_DIR_ABS" \
                PORTAL_PORT "$portal_port" \
                LANCE_PORT "$lance_port" \
                BIND_ADDR "$BIND" \
                LAB_NAME "$display_name" \
                LAB_SHORT_NAME "$slug" \
                LAB_THEME "$theme" \
                LAB_INTENT "$slug"
        fi
    else
        # First boot: allocate ports (or honor --port), write the pack once.
        if [[ -n "$PORT_OVERRIDE" ]]; then
            portal_port="$PORT_OVERRIDE"
            lance_port=$(( portal_port + INST_PORT_LANCE_OFFSET - INST_PORT_PORTAL_OFFSET ))
            # QA-5: a --port on first boot used to skip the registry-
            # collision check inst_allocate_ports itself performs, so two
            # Worlds could be permanently pinned to the same block.
            if ! _instance_validate_port_override "$slug" "$portal_port" "$lance_port"; then
                echo "✗"
                _instance_cleanup_and_exit 1
            fi
        else
            local alloc
            if ! alloc="$(inst_allocate_ports)"; then
                echo "✗"
                _instance_cleanup_and_exit 1
            fi
            portal_port="${alloc%% *}"
            lance_port="${alloc##* }"
        fi
        inst_write_env_pack "$slug" \
            ARAIL_INSTANCE "$slug" \
            ARAIL_ENV_FILE "$pack_file" \
            LAB_ROOT "$instance_root" \
            ARAIL_DATA_DIR "$instance_root/data" \
            LAB_PKB "$instance_root/pkb" \
            ARAIL_EXPERIMENTS_DIR "$instance_root/data/experiments" \
            ARAIL_MODELS_DIR "$_INST_MODELS_DIR_ABS" \
            ARAIL_WORLDS_DIR "$_INST_WORLDS_DIR_ABS" \
            PORTAL_PORT "$portal_port" \
            LANCE_PORT "$lance_port" \
            BIND_ADDR "$BIND" \
            LAB_NAME "$display_name" \
            LAB_SHORT_NAME "$slug" \
            LAB_THEME "$theme" \
            LAB_INTENT "$slug"
        if [[ -f "$REPO_ROOT/lab/data/secrets.env" ]]; then
            info "Provider keys are per-instance — add this instance's keys via ⚙ Manage providers."
        fi
    fi
    echo "✓"

    # From here on, this shell IS the instance: load the pinned pack.
    set -a
    # shellcheck disable=SC1091
    source "$pack_file"
    set +a

    # ── Dependent service: relational store readiness ───────────────
    # sprints/2026-08-10-arail2-persistence-instantiated §4.5. THIS
    # instance's data_dir only — never a sibling's. SAFE-FORWARD applies
    # and is reported; anything lossy/ahead/diverged warns, names the
    # exact verb, and start continues (nothing reads arail.db at runtime
    # yet — refusing to boot a working World over an inert store would
    # trade a real outage for a theoretical one; see BACKLOG.md for the
    # promotion-to-hard-gate follow-up once a runtime reader lands).
    _instance_db_ensure "${ARAIL_DATA_DIR:-$(inst_data_dir "$slug")}"

    # ── [5/8] Bind ports ─────────────────────────────────────────────
    printf '[5/8] Bind ports… '
    inst_load_port_helpers
    if _port_in_use "$portal_port" || _port_in_use "$lance_port"; then
        echo "✗"
        echo "  port ${portal_port}/${lance_port} already taken — try: lsof -iTCP:${portal_port} -sTCP:LISTEN" >&2
        _instance_cleanup_and_exit 1
    fi
    echo "✓"

    # ── [6/8] Portal up ──────────────────────────────────────────────
    printf '[6/8] Portal up… '
    local instance_token
    instance_token="$(python3 -c 'import uuid; print(uuid.uuid4())')"
    # ARCHITECTURE.md §11.1: --warm passes ARAIL_TIER0_BOOT_WARM=1 on THIS
    # invocation only (exactly how ARAIL_INSTANCE_TOKEN is already passed) —
    # the env pack (instance.env) is never touched, since a warm flag is
    # per-invocation, not per-instance state. `export` (not an inline
    # prefix) so the bash-3.2 "empty array under set -u" class of landmine
    # (ARCHITECTURE.md A2 / this sprint's WP4 notes) never applies here.
    if [[ "$WARM_UP" == "1" ]]; then
        export ARAIL_TIER0_BOOT_WARM=1
    fi
    ARAIL_INSTANCE_TOKEN="$instance_token" uvicorn arail.portal.app:app \
        --host "$BIND" --port "$portal_port" \
        --log-level warning >> "$(inst_log_dir "$slug")/portal.log" 2>&1 &
    local portal_pid=$!
    _INST_PIDS+=("$portal_pid")

    # REVIEW.md M1: an HTTP 200 alone does not prove OUR instance is
    # answering — §3.5 stage 5 / §2.3 step 4 require the response's token
    # AND checkout to match, so a foreign process that grabbed the port
    # first (or a stale process from a different checkout) is never
    # mistaken for a successful boot. The registry record is only written
    # after this check passes (§2.2's "a record's existence means this
    # instance was, at some point, actually serving").
    # QA-B1: `curl -sf` alone collapses "gated" (401), "crashed" (5xx), and
    # "not listening yet" (connection refused/timeout) into the same empty
    # string, so a future onboarding_gate regression would burn the full
    # 60s cap and report the generic "portal did not come up" — naming the
    # wrong cause, exactly like this defect did. `-w '\n%{http_code}'`
    # rides alongside `-sf` (curl still writes the format string on a
    # failed/refused request; `-f` only affects whether the BODY is kept)
    # so a real HTTP error status is distinguishable from no answer at
    # all, without weakening the existing -f/-m 0.7 behaviour.
    local waited=0 portal_ready=0 portal_mismatch=0 portal_last_status=""
    while (( waited < 240 )); do  # 240 * 0.25s = 60s cap
        if ! kill -0 "$portal_pid" 2>/dev/null; then
            break
        fi
        local probe_raw probe_status probe_body
        probe_raw="$(curl -sf -m 0.7 -w '\n%{http_code}' "http://${BIND}:${portal_port}/api/instance" 2>/dev/null || true)"
        probe_status="${probe_raw##*$'\n'}"
        probe_body="${probe_raw%$'\n'*}"
        [[ -n "$probe_status" && "$probe_status" != "000" ]] && portal_last_status="$probe_status"
        if [[ -n "$probe_body" ]]; then
            local probe_token probe_checkout
            probe_token="$(_json_field "$probe_body" token)"
            probe_checkout="$(_json_field "$probe_body" checkout)"
            if [[ -n "$probe_token" && "$probe_token" == "$instance_token" \
                  && -n "$probe_checkout" && "$probe_checkout" == "$REPO_ROOT" ]]; then
                portal_ready=1
            else
                portal_mismatch=1
            fi
            break
        fi
        sleep 0.25
        waited=$((waited + 1))
    done
    if [[ "$portal_mismatch" == "1" ]]; then
        echo "✗"
        echo "  port ${portal_port} was taken during startup — a different process answered /api/instance (token/checkout mismatch): try lsof -iTCP:${portal_port} -sTCP:LISTEN" >&2
        _instance_cleanup_and_exit 1
    fi
    if [[ "$portal_ready" != "1" ]]; then
        echo "✗"
        if [[ -n "$portal_last_status" && "$portal_last_status" != "200" ]]; then
            echo "  /api/instance answered HTTP ${portal_last_status}, not 200 — check onboarding_gate's allow-list: tail $(inst_log_dir "$slug")/portal.log" >&2
        else
            echo "  portal did not come up — tail $(inst_log_dir "$slug")/portal.log" >&2
        fi
        tail -n 30 "$(inst_log_dir "$slug")/portal.log" >&2 2>/dev/null || true
        _instance_cleanup_and_exit 1
    fi
    echo "✓"

    # ── [7/8] Memory up ──────────────────────────────────────────────
    printf '[7/8] Memory up… '
    uvicorn arail.memory_service:app \
        --host "$BIND" --port "$lance_port" \
        --log-level warning >> "$(inst_log_dir "$slug")/memory.log" 2>&1 &
    local memory_pid=$!
    _INST_PIDS+=("$memory_pid")
    local mem_waited=0 mem_ready=0
    while (( mem_waited < 80 )); do  # 80 * 0.25s = 20s cap
        if ! kill -0 "$memory_pid" 2>/dev/null; then break; fi
        # QA-4: GET / has no route on the memory service (404); the probe
        # must target the route the service actually serves.
        if curl -sf -m 0.7 "http://${BIND}:${lance_port}/health" >/dev/null 2>&1; then mem_ready=1; break; fi
        sleep 0.25
        mem_waited=$((mem_waited + 1))
    done
    if [[ "$mem_ready" == "1" ]]; then
        echo "✓"
    else
        echo "⚠"
        warn "memory service did not answer within 20s — chat works, memory features degrade."
    fi

    # Ollama — machine-shared, unchanged. Started only if unreachable, owned
    # via a pidfile in THIS instance's data dir (mirrors start.sh's root-lab
    # block; never pattern-matched, never touches an Ollama we didn't start).
    if command -v ollama &>/dev/null; then
        if curl -sf -m 2 "http://${OLLAMA_HOST:-127.0.0.1:11434}/api/version" >/dev/null 2>&1; then
            info "Ollama     → http://${OLLAMA_HOST:-127.0.0.1:11434} (already running)"
            _check_default_ollama_model
        else
            ollama serve >> "$(inst_log_dir "$slug")/ollama.log" 2>&1 &
            local ollama_pid=$!
            # NOT added to _INST_PIDS (REVIEW.md M3) — Ollama is
            # machine-shared; _INST_OLLAMA_PID is killed by
            # _instance_cleanup_and_exit only when no sibling instance is
            # still alive, never unconditionally alongside portal/memory.
            _INST_OLLAMA_PID="$ollama_pid"
            mkdir -p "$(inst_data_dir "$slug")"
            echo "$ollama_pid" > "$(inst_data_dir "$slug")/.ollama-started-by-arail.pid"
            # Same 45s-cap-with-progress treatment as the root-lab Ollama
            # block below, and the same warn-on-cap-miss the memory-service
            # probe above already does — the old fixed 10 x 0.5s = 5s window
            # gave up silently here, leaving chat hit a bare "Connection
            # refused" with no sign anything was still loading.
            local ollama_waited=0 ollama_ready=0
            local ollama_timeout="${ARAIL_OLLAMA_READY_TIMEOUT:-45}"
            while (( ollama_waited < ollama_timeout )); do
                if curl -sf -m 1 "http://${OLLAMA_HOST:-127.0.0.1:11434}/api/version" >/dev/null 2>&1; then
                    ollama_ready=1; break
                fi
                sleep 1
                ollama_waited=$((ollama_waited + 1))
            done
            if [[ "$ollama_ready" == "1" ]]; then
                info "Ollama     → http://${OLLAMA_HOST:-127.0.0.1:11434} (ready)"
                _check_default_ollama_model
            else
                warn "Ollama     → did not respond within ${ollama_timeout}s — chat will show"
                warn "             \"Connection refused\" until it comes up. Check progress with:"
                warn "             tail -f $(inst_log_dir "$slug")/ollama.log"
                warn "             (slow machine? raise the wait: ARAIL_OLLAMA_READY_TIMEOUT=90)"
            fi
        fi
    else
        info "Ollama     → (not installed — chat's default local model needs it: https://ollama.com)"
    fi

    # ── [8/8] World bound + index ────────────────────────────────────
    printf '[8/8] World bound + index… '
    local mount_out mount_rc=0
    mount_out="$(python -m arail.world_mount mount "$bundle_dir" 2>&1)" || mount_rc=$?
    if [[ "$mount_rc" != "0" ]]; then
        echo "⚠"
        warn "World mount failed — instance is up unmounted; /worlds can retry."
        warn "$(printf '%s' "$mount_out" | tail -n 3)"
    else
        local staged_dir term_n
        staged_dir="$(inst_pkb_dir "$slug")/sources/world-${slug}"
        term_n="$(find "$staged_dir" -type f -name '*.md' 2>/dev/null | wc -l | tr -d ' ')"
        echo "✓ (${term_n} term(s) staged)"
    fi

    # ── Record + URL ─────────────────────────────────────────────────
    local checkout record_json now
    checkout="$REPO_ROOT"
    now="$(python3 -c 'import datetime; print(datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))')"
    record_json="$(python3 -c '
import json, sys
print(json.dumps({
    "schema": "arail.instance-registry/v1",
    "slug": sys.argv[1], "display_name": sys.argv[2], "checkout": sys.argv[3],
    "instance_root": sys.argv[4], "data_dir": sys.argv[5], "pkb_root": sys.argv[6],
    "bind": sys.argv[7], "portal_port": int(sys.argv[8]), "lance_port": int(sys.argv[9]),
    "launcher_pid": int(sys.argv[10]), "portal_pid": int(sys.argv[11]), "memory_pid": int(sys.argv[12]),
    "token": sys.argv[13], "started_at": sys.argv[14], "arailctl_version": sys.argv[15],
}))
' "$slug" "$display_name" "$checkout" "$instance_root" "$(inst_data_dir "$slug")" "$(inst_pkb_dir "$slug")" \
      "$BIND" "$portal_port" "$lance_port" "$$" "$portal_pid" "$memory_pid" \
      "$instance_token" "$now" "concurrent-worlds-wp4")"
    inst_write_record "$slug" "$record_json"
    # The picker's memory — recorded only now, AFTER the portal answered and
    # the record was written. Recording it earlier would make a World that
    # crash-loops on boot the sticky default, which is precisely backwards.
    inst_write_last_target world "$slug"
    rm -f "$_INST_CLAIM_FILE" 2>/dev/null || true
    _INST_CLAIM_FILE=""

    local url="http://${BIND}:${portal_port}"
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "  ${BOLD}${display_name} is running.${RESET}  Press Ctrl+C to stop."
    echo ""
    echo -e "  Dashboard:  ${BOLD}${url}${RESET}"
    echo -e "  Data root:  ${BOLD}${instance_root}${RESET}"
    echo ""
    python3 -m arail.model_defaults --banner 2>/dev/null || true
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

    # ARCHITECTURE.md §11.1: after the record is written and the URL banner
    # printed — never before — and only when --warm was given.
    [[ "$WARM_UP" == "1" ]] && _warm_report "http://${BIND}:${portal_port}/api/instance"

    if [[ "${ARAIL_NO_BROWSER:-0}" != "1" ]] && [[ -t 1 ]]; then
        (
            for _ in 1 2 3 4 5 6 7 8 9 10; do
                curl -sf -o /dev/null "$url" 2>/dev/null && break
                sleep 0.5
            done
            if command -v open >/dev/null; then open "$url"
            elif command -v xdg-open >/dev/null; then xdg-open "$url" >/dev/null 2>&1
            fi
        ) &
    fi

    # Clear the M4 EXIT trap here (REVIEW.md's prescribed fix): the launch
    # has now genuinely succeeded (record written, claim already removed
    # above) — the ONLY intended exit paths from here on are INT/TERM,
    # which are already handled explicitly. An EXIT trap left armed past
    # this point would double-invoke _instance_cleanup_and_exit on every
    # ordinary Ctrl-C (once from the TERM/INT trap, once from EXIT as the
    # shell unwinds) — harmless (idempotent) but noisy, and no longer
    # needed since the claim it existed to protect is already gone.
    trap - EXIT
    trap '_instance_cleanup_and_exit 0' INT TERM
    wait
}

if [[ -n "$TARGET_SLUG" ]]; then
    _instance_start "$TARGET_SLUG"
    exit 0
fi

# =============================================================================
#  Legacy root lab (VISION §3: |W|==0, or the operator picked the root lab
#  from the picker). ARCHITECTURE.md §8.2 (sprints/2026-07-29-elite-cli)
#  ADDS a readiness gate, a pre-spawn port check, and an early cleanup
#  trap here — additions and one moved line, not a rewrite; the spawn
#  order and every existing message below are unchanged.
# =============================================================================

PIDS=()

# ARCHITECTURE.md §8.2 step 1: arm the cleanup trap IMMEDIATELY after
# PIDS=() — it used to be armed only after the (unconditional) success
# banner, below. Without moving it up here FIRST, the new "portal never
# came up -> exit 1" path (added below, after the spawns) would leak every
# already-spawned child (F1's "kill list is ${PIDS[@]} only" still holds —
# this only changes WHEN the trap is armed, never what it kills). Same
# lesson `_instance_start` already learned as REVIEW.md M4 (the EXIT-trap
# fix), applied here to the root path for the first time.
cleanup() {
    echo ""
    info "Shutting down…"
    # REVIEW.md m1 / bash 3.2 (A2, same class as B1): `"${PIDS[@]}"` on a
    # ZERO-element array aborts under `set -u`. The trap is armed here
    # before the first `PIDS+=` (below), so a signal in that ~100-300ms
    # window — before any child has spawned — hits this with PIDS still
    # empty. Guard the count explicitly, matching the idiom used at
    # `_restart_start_argv`/`ORIGINAL_ARGV`.
    if (( ${#PIDS[@]} > 0 )); then
        for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
    fi
    wait 2>/dev/null || true
    # Only remove the pidfile if we're the ones who wrote it — a plain
    # `[[ -f ]]` check would also fire (harmlessly) if a separate
    # ./arailctl stop already cleaned it up first.
    [[ -n "${OLLAMA_PID:-}" && -f "${OLLAMA_PIDFILE:-}" ]] && rm -f "$OLLAMA_PIDFILE"
    info "All services stopped."
}
trap cleanup INT TERM

# ARCHITECTURE.md §8.2 / F31: the instance path has always refused a bind
# conflict BEFORE spawning (stage [5/8]); the root path never has. Without
# this, a second `./arailctl start` against an already-running root lab
# would spawn a doomed uvicorn, and the NEW readiness gate below would see
# the FIRST (already-running) lab answering /api/instance and misreport
# success — "readiness passes because a previous root lab is still
# listening" (F31's exact failure mode).
inst_load_port_helpers
if declare -F _port_in_use >/dev/null 2>&1 && _port_in_use "${PORTAL_PORT:-8080}"; then
    echo "Root lab already running — try: ./arailctl status" >&2
    echo "  (or a foreign process is on :${PORTAL_PORT:-8080} — lsof -iTCP:${PORTAL_PORT:-8080} -sTCP:LISTEN)" >&2
    exit 1
fi

# Dependent service: relational store readiness, root lab's own data_dir
# only. sprints/2026-08-10-arail2-persistence-instantiated §4.5.
_instance_db_ensure "${ARAIL_DATA_DIR:-$REPO_ROOT/lab/data}"

echo ""
echo -e "${CYAN}${BOLD}${LAB_LOGO} Starting lab services…${RESET}"
echo ""

info "Portal     → http://${BIND}:${PORTAL_PORT:-8080}"
# ARCHITECTURE.md §11.1: same --warm wiring as the instance path above —
# ARAIL_TIER0_BOOT_WARM=1 for THIS process only, via export (not an inline
# prefix) for the same bash-3.2 reason documented at the instance path's
# call site.
if [[ "$WARM_UP" == "1" ]]; then
    export ARAIL_TIER0_BOOT_WARM=1
fi
# --app-dir "$REPO_ROOT" is functionally a no-op (uvicorn already defaults
# --app-dir to cwd, and this script already `cd`s to REPO_ROOT above) — it
# is here so the process's argv carries a checkout-scoped, grep-able
# marker (QA-11: reset.sh's stop_services() patterns match on it).
uvicorn arail.portal.app:app \
    --app-dir "$REPO_ROOT" \
    --host "$BIND" --port "${PORTAL_PORT:-8080}" \
    --log-level warning &
_ROOT_PORTAL_PID=$!
PIDS+=("$_ROOT_PORTAL_PID")

# Ollama backs the default chat model (llama-ai-eng) regardless of
# MODEL_BACKEND/tier — setup.sh installs it and pulls/creates the model,
# but `ollama pull`/`ollama create` only auto-launch the daemon as a
# side effect *at setup time*; nothing keeps it running afterward, so a
# fresh terminal session (or a reboot) leaves chat unable to reach it.
# We only ever manage an Ollama we started ourselves here — an Ollama
# the user runs independently (brew services, their own `ollama serve`,
# another project) is left alone entirely, matching stop_services()'s
# "never touch what we didn't start" rule in reset.sh. A PID file
# (removed by the cleanup trap below, and by `./arailctl stop` via
# reset.sh) records that this session is the owner.
# Same precedence + tilde-expansion as reset.sh's _resolve_data_dir() —
# must agree exactly, since reset.sh's stop_services() reads this pidfile.
_expand_tilde_for_ollama() {
    case "${1-}" in
        "~")   printf '%s' "$HOME" ;;
        "~/"*) printf '%s%s' "$HOME" "${1#\~}" ;;
        *)     printf '%s' "${1-}" ;;
    esac
}
if [[ -n "${ARAIL_DATA_DIR:-}" ]]; then
    OLLAMA_DATA_DIR="$(_expand_tilde_for_ollama "$ARAIL_DATA_DIR")"
else
    OLLAMA_DATA_DIR="$(_expand_tilde_for_ollama "${LAB_ROOT:-lab}")"
    OLLAMA_DATA_DIR="${OLLAMA_DATA_DIR%/}/data"
fi
OLLAMA_PIDFILE="${OLLAMA_DATA_DIR%/}/.ollama-started-by-arail.pid"
# Same LAB_ROOT convention as reset.sh's _resolve_data_dir() (default "lab").
ROOT_LOG_DIR="$(_expand_tilde_for_ollama "${LAB_ROOT:-lab}")"
ROOT_LOG_DIR="${ROOT_LOG_DIR%/}/logs"
mkdir -p "$ROOT_LOG_DIR"

# How long to wait for `ollama serve`'s HTTP API to come up before giving
# up and starting the rest of the lab anyway. The old fixed 5s window
# (10 x 0.5s) moved on silently once it elapsed, so a slow first-run cold
# start (GPU probing, spinning up the runner) left chat hitting a bare
# "Connection refused" with zero indication anything was still loading.
OLLAMA_READY_TIMEOUT="${ARAIL_OLLAMA_READY_TIMEOUT:-45}"

_wait_for_root_ollama() {
    local waited=0
    while (( waited < OLLAMA_READY_TIMEOUT )); do
        curl -sf -m 1 "http://${OLLAMA_HOST:-127.0.0.1:11434}/api/version" >/dev/null 2>&1 && return 0
        sleep 1
        waited=$(( waited + 1 ))
        if (( waited % 5 == 0 )); then
            info "Ollama     → still starting… (${waited}s/${OLLAMA_READY_TIMEOUT}s)"
        fi
    done
    return 1
}

if command -v ollama &>/dev/null; then
    if curl -sf -m 2 "http://${OLLAMA_HOST:-127.0.0.1:11434}/api/version" >/dev/null 2>&1; then
        info "Ollama     → http://${OLLAMA_HOST:-127.0.0.1:11434} (already running)"
        _check_default_ollama_model
    else
        info "Ollama     → http://${OLLAMA_HOST:-127.0.0.1:11434} (starting — first launch can take up to ${OLLAMA_READY_TIMEOUT}s)"
        ollama serve >"$ROOT_LOG_DIR/ollama.out.log" 2>"$ROOT_LOG_DIR/ollama.err.log" &
        OLLAMA_PID=$!
        PIDS+=("$OLLAMA_PID")
        mkdir -p "$(dirname "$OLLAMA_PIDFILE")"
        echo "$OLLAMA_PID" > "$OLLAMA_PIDFILE"
        if _wait_for_root_ollama; then
            info "Ollama     → ready"
            _check_default_ollama_model
        else
            warn "Ollama     → still not responding after ${OLLAMA_READY_TIMEOUT}s — chat will show"
            warn "             \"Connection refused\" until it comes up. It's still starting in the"
            warn "             background; check progress with: tail -f ${ROOT_LOG_DIR}/ollama.err.log"
            warn "             (slow machine? raise the wait: ARAIL_OLLAMA_READY_TIMEOUT=90 ./arailctl start)"
        fi
    fi
else
    info "Ollama     → (not installed — chat's default local model needs it: https://ollama.com)"
fi

if [[ "${MODEL_BACKEND:-auto}" == "mlx" ]]; then
    info "MLX API    → http://${BIND}:${MLX_OPENAI_PORT:-11435}/v1 (starting)"
    uvicorn arail.mlx_openai_server:app \
        --app-dir "$REPO_ROOT" \
        --host "$BIND" --port "${MLX_OPENAI_PORT:-11435}" \
        --log-level warning >"$ROOT_LOG_DIR/mlx-api.out.log" 2>"$ROOT_LOG_DIR/mlx-api.err.log" &
    _ROOT_MLX_PID=$!
    PIDS+=("$_ROOT_MLX_PID")
    # Warm the model in the background. MLXBackend() loads weights on
    # first use, so /health's first hit pays that cost — do it here,
    # off the critical path, so it's the server warming itself up while
    # the rest of the lab starts, not the user's first chat message.
    (
        for _ in $(seq 1 20); do
            curl -sf -m 2 "http://${BIND}:${MLX_OPENAI_PORT:-11435}/health" >/dev/null 2>&1 && break
            sleep 1
        done
    ) &
fi

info "Memory     → http://${BIND}:${LANCE_PORT}"
uvicorn arail.memory_service:app \
    --app-dir "$REPO_ROOT" \
    --host "$BIND" --port "$LANCE_PORT" \
    --log-level warning &
_ROOT_MEMORY_PID=$!
PIDS+=("$_ROOT_MEMORY_PID")

if command -v ttyd &>/dev/null; then
    info "Terminal   → http://${BIND}:${TERMINAL_PORT:-7681}"
    # Terminal persistence: when tmux is available we attach every ttyd
    # connection to a named session ("arail") so closing the browser
    # tab, navigating away, or reloading the iframe doesn't nuke the
    # user's scrollback, pwd, running jobs, or env. New connections
    # reattach to the same session (`-A` = attach or create).
    # -t disableLeaveAlert=true suppresses ttyd's built-in
    # "Are you sure you want to leave?" prompt that used to fire on
    # every nav click. tmux already keeps the session alive, so the
    # warning is noise — not protection — inside the portal.
    TTYD_OPTS=(-W -p "${TERMINAL_PORT:-7681}" -i "$BIND"
               -t disableLeaveAlert=true
               -t scrollBar=true
               -t scrollback=250000)
    if command -v tmux &>/dev/null; then
        TMUX_SHELL="${SHELL:-/bin/bash}"
        ttyd "${TTYD_OPTS[@]}" \
            tmux new-session -A -s "${LAB_SHORT_NAME:-arail}" \
                "$TMUX_SHELL" &
    else
        warn "tmux not installed — terminal scrollback won't survive iframe reloads"
        warn "install: brew install tmux (mac) · sudo apt install tmux (linux)"
        ttyd "${TTYD_OPTS[@]}" "${SHELL:-bash}" &
    fi
    _ROOT_TERMINAL_PID=$!
    PIDS+=("$_ROOT_TERMINAL_PID")
else
    platform_hint=""
    if [[ "$(uname -s)" == "Darwin" ]]; then
        platform_hint="  brew install ttyd"
    elif command -v apt >/dev/null 2>&1; then
        platform_hint="  sudo apt install ttyd"
    elif command -v dnf >/dev/null 2>&1; then
        platform_hint="  sudo dnf install ttyd"
    elif command -v pacman >/dev/null 2>&1; then
        platform_hint="  sudo pacman -S ttyd"
    elif command -v emerge >/dev/null 2>&1; then
        platform_hint="  sudo emerge -av www-apps/ttyd"
    fi
    info "Terminal   → (ttyd not installed — /terminal shows install help)"
    [[ -n "$platform_hint" ]] && info "             install: ${platform_hint}"
fi

if command -v jupyter &>/dev/null; then
    info "Notebook   → http://${BIND}:${NOTEBOOK_PORT:-8888}"
    # Ensure Dark High Contrast theme is the default
    _jl_settings="$(python3 -c 'import jupyterlab,os;print(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(jupyterlab.__file__)))),"share","jupyter","lab","settings"))' 2>/dev/null || echo "")"
    if [[ -n "$_jl_settings" ]]; then
        mkdir -p "$_jl_settings"
        cat > "$_jl_settings/overrides.json" <<'JLTHEME'
{
  "@jupyterlab/apputils-extension:themes": {
    "theme": "JupyterLab Dark High Contrast"
  }
}
JLTHEME
    fi
    jupyter lab \
        --no-browser \
        --ip="$BIND" \
        --port="${NOTEBOOK_PORT:-8888}" \
        --NotebookApp.token="" \
        --NotebookApp.password="" \
        --ServerApp.tornado_settings='{"headers":{"Content-Security-Policy":"frame-ancestors '\''self'\'' http://127.0.0.1:* http://localhost:*"}}' \
        >"$ROOT_LOG_DIR/notebook.out.log" 2>"$ROOT_LOG_DIR/notebook.err.log" &
    _ROOT_NOTEBOOK_PID=$!
    PIDS+=("$_ROOT_NOTEBOOK_PID")
else
    info "Notebook   → (jupyter not installed — skipping)"
fi

if command -v code-server &>/dev/null; then
    info "IDE        → http://${BIND}:${IDE_PORT:-8443}"
    code-server \
        --bind-addr "${BIND}:${IDE_PORT:-8443}" \
        --auth password \
        --disable-telemetry \
        . &
    _ROOT_IDE_PID=$!
    PIDS+=("$_ROOT_IDE_PID")
else
    info "IDE        → (code-server not installed — skipping)"
fi

# ── NEW readiness phase (ARCHITECTURE.md §8.2) ──────────────────────────
# Portal is REQUIRED: unlike everything below, its failure aborts the
# launch (cleanup + exit 1) rather than degrading. This ports the instance
# path's stage [6/8] identity-gated readiness probe to the root process
# (A7: root has no token, so svc_identity_root's slug=="root" + checkout
# match stands in for the instance path's token+checkout pair) — the root
# path has never had ANY readiness detection before this.
echo ""
echo -e "${BOLD}Readiness:${RESET}"
_root_have_curl=0
command -v curl >/dev/null 2>&1 && _root_have_curl=1
_root_portal_url="http://${BIND}:${PORTAL_PORT:-8080}/api/instance"
_root_portal_ready=0
_root_portal_last_status=""
if [[ "$_root_have_curl" == "1" ]] && declare -F svc_wait_http_ready >/dev/null 2>&1; then
    if _root_portal_last_status="$(svc_wait_http_ready "$_root_portal_url" 600 "$_ROOT_PORTAL_PID")"; then
        _root_portal_ready=1
    fi
fi
if [[ "$_root_portal_ready" == "1" ]]; then
    _root_portal_body="$(curl -sf -m 0.7 "$_root_portal_url" 2>/dev/null || true)"
    if declare -F svc_identity_root >/dev/null 2>&1 && svc_identity_root "$_root_portal_body" "$REPO_ROOT"; then
        echo -e "  ${GREEN}✓${RESET} Portal     ${_root_portal_url%/api/instance}"
    else
        echo "  ✗ Portal     port ${PORTAL_PORT:-8080} is answered by a DIFFERENT checkout/process — try: lsof -iTCP:${PORTAL_PORT:-8080} -sTCP:LISTEN" >&2
        cleanup
        exit 1
    fi
else
    if [[ -n "$_root_portal_last_status" && "$_root_portal_last_status" != "000" ]]; then
        echo "  ✗ Portal     /api/instance answered HTTP ${_root_portal_last_status}, not 200 — see the uvicorn output above" >&2
    else
        echo "  ✗ Portal     did not come up — see the uvicorn output above" >&2
    fi
    cleanup
    exit 1
fi

# The rest degrade, never abort (ARCHITECTURE.md §8.2 table). Each helper
# call is set -e-safe (it's the condition of an `if`) and never touches a
# pid it did not spawn (F1) — only svc_* probes, no kill/pkill/pgrep.
_root_wait_http_degrade() {
    local label="$1" url="$2" cap_ds="$3" pid="$4" failmsg="$5"
    if [[ "$_root_have_curl" != "1" ]] || ! declare -F svc_wait_http_ready >/dev/null 2>&1; then
        echo "  ⚠ ${label}   curl not found — HTTP probes skipped"
        return 1
    fi
    if svc_wait_http_ready "$url" "$cap_ds" "$pid" >/dev/null; then
        echo -e "  ${GREEN}✓${RESET} ${label}   ${url%/health}"
        return 0
    fi
    echo "  ⚠ ${failmsg}"
    return 1
}

_root_wait_listen_degrade() {
    local label="$1" port="$2" cap_ds="$3" pid="$4" failmsg="$5" okurl="$6"
    if declare -F svc_wait_listening >/dev/null 2>&1 && svc_wait_listening "$port" "$cap_ds" "$pid"; then
        echo -e "  ${GREEN}✓${RESET} ${label}   ${okurl}"
        return 0
    fi
    echo "  ⚠ ${failmsg}"
    return 1
}

_root_memory_ok=0
if _root_wait_http_degrade "Memory  " "http://${BIND}:${LANCE_PORT}/health" 200 "$_ROOT_MEMORY_PID" \
    "memory service did not answer within 20s — chat works, memory features degrade."; then
    _root_memory_ok=1
fi

_root_mlx_ok=1
if [[ "${MODEL_BACKEND:-auto}" == "mlx" ]]; then
    _root_mlx_ok=0
    if _root_wait_http_degrade "MLX     " "http://${BIND}:${MLX_OPENAI_PORT:-11435}/health" 200 "${_ROOT_MLX_PID:-}" \
        "MLX API unavailable — chat falls back per router config."; then
        _root_mlx_ok=1
    fi
fi

_root_terminal_ok=1
if command -v ttyd &>/dev/null; then
    _root_terminal_ok=0
    if _root_wait_listen_degrade "Terminal" "${TERMINAL_PORT:-7681}" 100 "${_ROOT_TERMINAL_PID:-}" \
        ":${TERMINAL_PORT:-7681} did not answer in 10s — the Terminal tab will show help" \
        "http://${BIND}:${TERMINAL_PORT:-7681}"; then
        _root_terminal_ok=1
    fi
fi

_root_notebook_ok=1
if command -v jupyter &>/dev/null; then
    _root_notebook_ok=0
    if _root_wait_listen_degrade "Notebook" "${NOTEBOOK_PORT:-8888}" 100 "${_ROOT_NOTEBOOK_PID:-}" \
        ":${NOTEBOOK_PORT:-8888} did not answer in 10s — the Notebook tab will show help. Check ${ROOT_LOG_DIR}/notebook.err.log (e.g. a stale venv interpreter after a Python upgrade)." \
        "http://${BIND}:${NOTEBOOK_PORT:-8888}"; then
        _root_notebook_ok=1
    fi
fi

_root_ide_ok=1
if command -v code-server &>/dev/null; then
    _root_ide_ok=0
    if _root_wait_listen_degrade "IDE     " "${IDE_PORT:-8443}" 100 "${_ROOT_IDE_PID:-}" \
        ":${IDE_PORT:-8443} did not answer in 10s — the IDE tab will show help" \
        "http://${BIND}:${IDE_PORT:-8443}"; then
        _root_ide_ok=1
    fi
fi

_root_degraded=()
[[ "$_root_memory_ok" == "1" ]] || _root_degraded+=("memory")
[[ "$_root_mlx_ok" == "1" ]] || _root_degraded+=("mlx")
[[ "$_root_terminal_ok" == "1" ]] || _root_degraded+=("terminal")
[[ "$_root_notebook_ok" == "1" ]] || _root_degraded+=("notebook")
[[ "$_root_ide_ok" == "1" ]] || _root_degraded+=("ide")

# The picker's memory — the root-lab counterpart of the instance path's
# call. Reached only after the required Portal readiness gate above passed
# (a portal that never answered exits 1 well before here), so the same
# "never make a failed launch sticky" rule holds.
inst_write_last_target root

echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
if (( ${#_root_degraded[@]} == 0 )); then
    echo -e "  ${BOLD}All services running.${RESET}  Press Ctrl+C to stop."
else
    _root_degraded_csv="$(IFS=,; echo "${_root_degraded[*]}")"
    _root_degraded_csv="${_root_degraded_csv//,/, }"
    echo -e "  ${BOLD}Lab running — degraded: ${_root_degraded_csv}.${RESET}  Press Ctrl+C to stop."
fi
echo ""
echo -e "  Dashboard:  ${BOLD}http://${BIND}:${PORTAL_PORT:-8080}${RESET}"
echo -e "  Ollama:     ${BOLD}http://${OLLAMA_HOST:-127.0.0.1:11434}${RESET}"
# URL block: only services that actually answered (ARCHITECTURE.md §8.2
# point 4 — kills the "URL block can lie" half of gap 6, and the
# MLX/Notebook/IDE lines that used to print unconditionally even when the
# binary isn't installed).
if [[ "${MODEL_BACKEND:-auto}" == "mlx" && "$_root_mlx_ok" == "1" ]]; then
    echo -e "  MLX API:    ${BOLD}http://${BIND}:${MLX_OPENAI_PORT:-11435}/v1${RESET}"
fi
[[ "$_root_memory_ok" == "1" ]] && echo -e "  Memory:     ${BOLD}http://${BIND}:${LANCE_PORT}${RESET}"
if command -v ttyd &>/dev/null && [[ "$_root_terminal_ok" == "1" ]]; then
    echo -e "  Terminal:   ${BOLD}http://${BIND}:${TERMINAL_PORT:-7681}${RESET}"
fi
if command -v jupyter &>/dev/null && [[ "$_root_notebook_ok" == "1" ]]; then
    echo -e "  Notebook:   ${BOLD}http://${BIND}:${NOTEBOOK_PORT:-8888}${RESET}"
fi
if command -v code-server &>/dev/null && [[ "$_root_ide_ok" == "1" ]]; then
    echo -e "  IDE:        ${BOLD}http://${BIND}:${IDE_PORT:-8443}${RESET}  (password in ${BOLD}lab.conf${RESET})"
fi
echo ""
python3 -m arail.model_defaults --banner 2>/dev/null || true
echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

# ARCHITECTURE.md §11.1: after the readiness banner — never before — and
# only when --warm was given. Same shared helper the instance path uses.
[[ "$WARM_UP" == "1" ]] && _warm_report "http://${BIND}:${PORTAL_PORT:-8080}/api/instance"

# Auto-open the dashboard unless suppressed or headless.
if [[ "${ARAIL_NO_BROWSER:-0}" != "1" ]] && [[ -t 1 ]]; then
    dashboard_url="http://${BIND}:${PORTAL_PORT:-8080}"
    (
        # Give uvicorn a moment to bind the port before opening the browser.
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            if curl -sf -o /dev/null "$dashboard_url" 2>/dev/null; then break; fi
            sleep 0.5
        done
        if command -v open >/dev/null; then open "$dashboard_url"
        elif command -v xdg-open >/dev/null; then xdg-open "$dashboard_url" >/dev/null 2>&1
        fi
    ) &
fi

# cleanup() + `trap cleanup INT TERM` are armed once, near the top of the
# root-lab block (right after PIDS=()) — see the ARCHITECTURE.md §8.2
# comment there for why arming it this early matters (F1).
wait

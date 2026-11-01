#!/usr/bin/env bash
# scripts/install.sh — refresh an already-provisioned lab: source, deps,
# components, models, verify. (sprints/2026-07-29-elite-cli/ARCHITECTURE.md
# §6, Ruling 1 — WP7.)
#
# `setup` = provision (creates .venv/.env/lab.conf, may prompt, may install
# OS packages). `install` = refresh what setup already provisioned — it
# requires a provisioned lab, never prompts (except one guarded confirmation
# before --rebuild-venv on a tty), and never touches OS packages or .env.
# `update` is a permanent alias (§6.4 — ARAIL is a forked blueprint; a verb
# removal breaks scripts silently). `tier`/`upgrade` are the separate
# feature-set axis — untouched here, see arailctl's own dispatch.
#
# Five phases, always in this order (selectable via --only/--skip):
#   [1/5] source     git pull --ff-only (clean+attached+tracking only)
#   [2/5] deps       pip install -e ".[$LAB_TIER]" (idempotent)
#   [3/5] components scripts/update.sh --apply --non-interactive
#   [4/5] models     detect drift vs the expected default chat model;
#                    apply only with --models
#   [5/5] verify     ./arailctl doctor
#
# Exit: 0 all phases ok/no-op · 3 degraded (a phase refused/failed, lab
# still usable; --check with pending changes) · 1 hard failure (deps
# refresh failed; not provisioned; lab live without --allow-running) ·
# 2 bad flags.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$REPO_ROOT"

# shellcheck disable=SC1091
[[ -f "$REPO_ROOT/scripts/lib/instances.sh" ]] && source "$REPO_ROOT/scripts/lib/instances.sh"

# ── ANSI color gating (ARCHITECTURE.md §13 "ANSI leaks into non-tty
# output", F25) — see arailctl's identical block for the full rationale.
# $'...' (ANSI-C quoting) so the variables hold real ESC bytes.
if [[ -t 1 && "${ARAIL_COLOR:-auto}" != "never" && -z "${NO_COLOR:-}" ]] || [[ "${ARAIL_COLOR:-auto}" == "always" ]]; then
    BOLD=$'\033[1m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; RED=$'\033[0;31m'; RESET=$'\033[0m'
else
    BOLD=""; GREEN=""; YELLOW=""; RED=""; RESET=""
fi

# shellcheck disable=SC1091
[[ -f .env ]] && set -a && source .env && set +a
LAB_TIER="${LAB_TIER:-minimalist}"
case "$LAB_TIER" in
    min) LAB_TIER="minimalist" ;;
    max) LAB_TIER="maximus" ;;
esac
LAB_MODE_EFF="${LAB_MODE:-${ARAIL_MODE:-airgapped}}"

# ── argv (preserved verbatim for the F5 re-exec — §14.1: arailctl/this
# script may inspect argv but never rewrites it for forwarding) ─────────
ORIGINAL_ARGV=("$@")

CHECK_MODE=0
ONLY_PHASES=""
SKIP_PHASES=""
WANT_MODELS=0
REBUILD_VENV=0
ALLOW_RUNNING=0
FORCE=0
ASSUME_YES=0
QUIET=0
JSON_MODE=0
POST_SOURCE_SHA=""   # --_post-source <sha> — internal, hidden (F5)

_install_usage() {
    cat <<EOF
Usage: ./arailctl install [--check|--dry-run] [--only <phase>[,<phase>]]
                           [--skip <phase>[,<phase>]] [--models]
                           [--rebuild-venv] [--allow-running] [--force]
                           [--yes|-y] [--quiet] [--json] [-h|--help]
  phases: source, deps, components, models, verify
EOF
}

_install_valid_phase() {
    case "$1" in
        source|deps|components|models|verify) return 0 ;;
        *) return 1 ;;
    esac
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check|--dry-run) CHECK_MODE=1; shift ;;
        --only)
            [[ $# -ge 2 && -n "${2:-}" ]] || { echo "--only requires a non-empty phase list" >&2; _install_usage >&2; exit 2; }
            ONLY_PHASES="$2"; shift 2 ;;
        --only=*)
            # TEST_REPORT.md Q6: an EMPTY --only= value used to bypass
            # _install_phase_list_valid entirely (guarded on `-n
            # "$ONLY_PHASES"`) and _install_phase_enabled then read the
            # empty list as "no filter" — so `--only=` silently ran all
            # five phases, including `deps`/`source`, instead of the "run
            # nothing" or usage-error an empty selector should mean.
            ONLY_PHASES="${1#--only=}"
            [[ -n "$ONLY_PHASES" ]] || { echo "--only requires a non-empty phase list" >&2; _install_usage >&2; exit 2; }
            shift ;;
        --skip)
            [[ $# -ge 2 && -n "${2:-}" ]] || { echo "--skip requires a non-empty phase list" >&2; _install_usage >&2; exit 2; }
            SKIP_PHASES="$2"; shift 2 ;;
        --skip=*)
            SKIP_PHASES="${1#--skip=}"
            [[ -n "$SKIP_PHASES" ]] || { echo "--skip requires a non-empty phase list" >&2; _install_usage >&2; exit 2; }
            shift ;;
        --models) WANT_MODELS=1; shift ;;
        --rebuild-venv) REBUILD_VENV=1; shift ;;
        --allow-running) ALLOW_RUNNING=1; shift ;;
        --force) FORCE=1; shift ;;
        --yes|-y) ASSUME_YES=1; shift ;;
        --quiet) QUIET=1; shift ;;
        --json) JSON_MODE=1; shift ;;
        --_post-source)
            [[ $# -ge 2 ]] || { echo "--_post-source requires a sha" >&2; exit 2; }
            POST_SOURCE_SHA="$2"; shift 2 ;;
        -h|--help) _install_usage; exit 0 ;;
        daemon)
            echo "install: 'install daemon' is not a thing — did you mean: ./arailctl install-daemon?" >&2
            exit 2 ;;
        -*)
            echo "Unknown flag: $1" >&2
            _install_usage >&2
            exit 2 ;;
        *)
            echo "Unknown argument: $1" >&2
            _install_usage >&2
            exit 2 ;;
    esac
done

if [[ -n "$ONLY_PHASES" && -n "$SKIP_PHASES" ]]; then
    echo "--only and --skip cannot be combined" >&2
    exit 2
fi

# ARCHITECTURE.md §5.3 (cross-cutting flag/env contract): ARAIL_QUIET=1 is
# the same as --quiet for setup AND install.
[[ "${ARAIL_QUIET:-0}" == "1" ]] && QUIET=1
_install_phase_list_valid() {
    local list="$1" p
    local -a _plist
    IFS=',' read -ra _plist <<< "$list"
    for p in "${_plist[@]}"; do
        _install_valid_phase "$p" || { echo "unknown phase: '$p' (valid: source, deps, components, models, verify)" >&2; exit 2; }
    done
}
[[ -n "$ONLY_PHASES" ]] && _install_phase_list_valid "$ONLY_PHASES"
[[ -n "$SKIP_PHASES" ]] && _install_phase_list_valid "$SKIP_PHASES"

# --yes is IMPLIED under a non-tty stdin (§5.1: "never prompts (--yes
# implied)") — the only prompt this script can ever reach is the guarded
# --rebuild-venv confirmation below.
EFFECTIVE_YES="$ASSUME_YES"
[[ ! -t 0 ]] && EFFECTIVE_YES=1

_install_line() {
    # §14.1: --json implies no human decoration on stdout — machine output
    # (the final JSON document) is the ONLY thing on stdout in that mode;
    # the human phase-by-phase narration moves to stderr instead of
    # disappearing (an operator running --json interactively still sees it).
    if [[ "$JSON_MODE" == "1" ]]; then
        echo "$*" >&2
    elif [[ "$QUIET" != "1" ]]; then
        echo "$*"
    fi
}

# ── Preflight (§4.3) ─────────────────────────────────────────────────────

# REVIEW.md m3: --_post-source alone used to be sufficient to skip this
# whole block (POST_SOURCE_SHA non-empty was the ONLY condition) — nothing
# validated the flag or marked it internal, so
# `./arailctl install --_post-source x --rebuild-venv` disabled the
# provisioned check AND the F21/F22 live-lab refusal for anyone who typed
# it. POST_SOURCE_ACTIVE additionally requires the env marker ONLY the F5
# re-exec's own `exec` line sets (inline, never exported, never
# documented) and that the sha names a real commit in THIS repo — either
# check failing means treat --_post-source as though it was never passed
# (run the full preflight), never silently trust an unverifiable claim.
POST_SOURCE_ACTIVE=0
if [[ -n "$POST_SOURCE_SHA" && "${_ARAIL_INSTALL_POST_SOURCE:-}" == "1" ]] \
    && git -C "$REPO_ROOT" cat-file -e "${POST_SOURCE_SHA}^{commit}" 2>/dev/null; then
    POST_SOURCE_ACTIVE=1
fi
unset _ARAIL_INSTALL_POST_SOURCE

if [[ "$POST_SOURCE_ACTIVE" != "1" ]]; then
    # Skipped on a GENUINE F5 re-exec leg — the ORIGINAL invocation already
    # passed every preflight check before it ever pulled.
    if [[ ! -d "$REPO_ROOT/.venv" || ! -f "$REPO_ROOT/.env" ]]; then
        echo "install: this lab is not provisioned yet — run: ./arailctl setup" >&2
        exit 1
    fi

    # F21/F22: refuse every mutating phase while a lab is live. Reuses
    # WP5's status collector (ARCHITECTURE.md §17: "use WP5's status
    # collector for the liveness preflight — do NOT grow a new liveness
    # check") rather than re-deriving liveness a fourth way.
    _install_lab_is_live() {
        local doc rc=0
        doc="$(bash "$REPO_ROOT/scripts/status.sh" --json=full --no-probe 2>/dev/null)" || rc=$?
        python3 -c '
import json, sys
try:
    doc = json.loads(sys.argv[1])
except Exception:
    # status.sh could not even emit valid JSON (F18 says it always
    # should) — fail SAFE: assume something might be live rather than
    # mutating blind.
    sys.exit(0)
if doc.get("root", {}).get("state") in ("up", "degraded"):
    sys.exit(0)
for inst in doc.get("instances", []):
    if inst.get("state") == "live":
        sys.exit(0)
sys.exit(1)
' "$doc"
    }
    if [[ "$ALLOW_RUNNING" != "1" ]] && _install_lab_is_live; then
        echo "install: the lab is currently running — stop it first: ./arailctl stop  (or pass --allow-running to proceed anyway)" >&2
        exit 1
    fi
fi

AIRGAPPED_BLOCKED=0
if [[ "$LAB_MODE_EFF" == "airgapped" && "$FORCE" != "1" ]]; then
    AIRGAPPED_BLOCKED=1
fi

_install_phase_enabled() {
    local p="$1"
    if [[ -n "$ONLY_PHASES" ]]; then
        case ",$ONLY_PHASES," in *",$p,"*) return 0 ;; *) return 1 ;; esac
    fi
    if [[ -n "$SKIP_PHASES" ]]; then
        case ",$SKIP_PHASES," in *",$p,"*) return 1 ;; *) return 0 ;; esac
    fi
    return 0
}

PHASE_DEGRADED=0
INSTALL_HARD_FAIL=0

# ── [1/5] source ─────────────────────────────────────────────────────────
_install_source_phase() {
    if [[ "$POST_SOURCE_ACTIVE" == "1" ]]; then
        # F5: this IS a verified re-exec leg (POST_SOURCE_ACTIVE, above) —
        # the pull already happened in the process that exec'd us. Report
        # it from the captured old sha rather than re-pulling (which would
        # be a no-op anyway, but would also lose the "old...new" range
        # this phase exists to report).
        local new_sha n
        new_sha="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo "?")"
        n="$(git -C "$REPO_ROOT" rev-list --count "${POST_SOURCE_SHA}..${new_sha}" 2>/dev/null || echo "?")"
        _install_line "  [1/5] source      ✓ ${POST_SOURCE_SHA:0:7}…${new_sha:0:7} (${n} commit(s)) — resumed after self-update"
        return
    fi
    if [[ "$AIRGAPPED_BLOCKED" == "1" ]]; then
        _install_line "  [1/5] source      ✗ refused — LAB_MODE=airgapped (use --force to override)"
        PHASE_DEGRADED=1; return
    fi
    if ! git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
        _install_line "  [1/5] source      ✗ refused — not a git repository"
        PHASE_DEGRADED=1; return
    fi
    if [[ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ]]; then
        _install_line "  [1/5] source      ✗ refused — worktree is dirty (uncommitted changes)"
        PHASE_DEGRADED=1; return
    fi
    if ! git -C "$REPO_ROOT" symbolic-ref -q HEAD >/dev/null 2>&1; then
        _install_line "  [1/5] source      ✗ refused — HEAD is detached"
        PHASE_DEGRADED=1; return
    fi
    local upstream
    if ! upstream="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref '@{u}' 2>/dev/null)"; then
        _install_line "  [1/5] source      ✗ refused — current branch has no upstream tracking branch"
        PHASE_DEGRADED=1; return
    fi
    if [[ "$CHECK_MODE" == "1" ]]; then
        # Dry run: a read-only `git fetch` (network, still respects the
        # airgap refusal above) then compare — no working-tree mutation.
        if ! git -C "$REPO_ROOT" fetch --quiet 2>/dev/null; then
            _install_line "  [1/5] source      ⚠ could not fetch — see above"
            PHASE_DEGRADED=1; return
        fi
        local behind
        behind="$(git -C "$REPO_ROOT" rev-list --count "HEAD..${upstream}" 2>/dev/null || echo 0)"
        if [[ "$behind" != "0" ]]; then
            _install_line "  [1/5] source      ⚠ ${behind} commit(s) behind ${upstream}"
            PHASE_DEGRADED=1
        else
            _install_line "  [1/5] source      ✓ up to date with ${upstream}"
        fi
        return
    fi
    # F28: print branch + toplevel BEFORE pulling — a git worktree (this
    # sprint's own build environment) is a legitimate, correct place to
    # run install from; --ff-only cannot cross branches regardless, but
    # the phase output stays honest about exactly what was pulled.
    local branch toplevel old_sha new_sha pull_out pull_rc=0
    branch="$(git -C "$REPO_ROOT" symbolic-ref --short HEAD 2>/dev/null || echo "?")"
    toplevel="$(git -C "$REPO_ROOT" rev-parse --show-toplevel 2>/dev/null || echo "$REPO_ROOT")"
    _install_line "      branch: ${branch}  toplevel: ${toplevel}"
    old_sha="$(git -C "$REPO_ROOT" rev-parse HEAD)"
    pull_out="$(git -C "$REPO_ROOT" pull --ff-only 2>&1)" || pull_rc=$?
    if (( pull_rc != 0 )); then
        _install_line "  [1/5] source      ✗ refused — diverged from ${upstream} (not a fast-forward)"
        PHASE_DEGRADED=1; return
    fi
    new_sha="$(git -C "$REPO_ROOT" rev-parse HEAD)"
    if [[ "$old_sha" == "$new_sha" ]]; then
        _install_line "  [1/5] source      ✓ up to date (${new_sha:0:7})"
        return
    fi
    local n
    n="$(git -C "$REPO_ROOT" rev-list --count "${old_sha}..${new_sha}")"
    _install_line "  [1/5] source      ✓ ${old_sha:0:7}…${new_sha:0:7} (${n} commit(s))"
    # F5: git just replaced install.sh's (and arailctl's) OWN bytes on
    # disk — this process may still be reading the OLD ones. Re-exec from
    # the NEW file immediately, carrying the pre-pull sha so the fresh
    # process can report the phase result without re-pulling (which would
    # be a no-op that loses the "old...new" range). --_post-source also
    # prevents a re-exec loop: the fresh process takes the branch above,
    # not this one.
    #
    # REVIEW.md B1 / bash 3.2 (A2): `"${arr[@]}"` on a ZERO-element array
    # aborts under `set -u` with "unbound variable" — the exact case for a
    # bare `./arailctl install` (the flagship, zero-flag invocation), whose
    # ORIGINAL_ARGV is empty. Same class WP4 already fixed for
    # `_restart_start_argv` in arailctl; `"${ORIGINAL_ARGV[@]:-}"` is NOT a
    # safe substitute (it silently turns "zero args" into "one empty-string
    # arg", which this script's own parser would then reject as an unknown
    # argument). Guard the count explicitly instead.
    # REVIEW.md m3: --_post-source alone used to be trusted at face value,
    # bypassing BOTH the provisioned check and the F21/F22 live-lab
    # refusal for anyone who typed it — a documented-`--help`-adjacent
    # flag, reachable by name even though it's internal. _ARAIL_INSTALL_POST_SOURCE=1
    # is set ONLY on this exec's own environment (inline on the command,
    # never exported by anything else, never documented) — the re-exec'd
    # process requires it AND a sha that names a real commit before it
    # will skip the preflight; either check failing makes the fresh
    # process treat --_post-source as though it was never passed.
    if (( ${#ORIGINAL_ARGV[@]} > 0 )); then
        _ARAIL_INSTALL_POST_SOURCE=1 \
            exec bash "$REPO_ROOT/scripts/install.sh" --_post-source "$old_sha" "${ORIGINAL_ARGV[@]}"
    else
        _ARAIL_INSTALL_POST_SOURCE=1 \
            exec bash "$REPO_ROOT/scripts/install.sh" --_post-source "$old_sha"
    fi
}

# ── [2/5] deps ───────────────────────────────────────────────────────────
_install_deps_phase() {
    if [[ "$AIRGAPPED_BLOCKED" == "1" ]]; then
        _install_line "  [2/5] deps        ✗ refused — LAB_MODE=airgapped (use --force to override)"
        PHASE_DEGRADED=1; return
    fi
    if [[ "$CHECK_MODE" == "1" ]]; then
        _install_line "  [2/5] deps        — not checked in --check mode (would run: pip install -e \".[${LAB_TIER}]\")"
        return
    fi
    if [[ "$REBUILD_VENV" == "1" ]]; then
        if [[ -t 0 && "$EFFECTIVE_YES" != "1" ]]; then
            read -rp "  This will delete .venv and reinstall — continue? [y/N] " _install_confirm
            if [[ ! "$_install_confirm" =~ ^[Yy] ]]; then
                echo "install: aborted (declined --rebuild-venv confirmation)" >&2
                exit 1
            fi
        fi
        _install_line "      rebuilding .venv…"
        rm -rf "$REPO_ROOT/.venv"
        if ! python3 -m venv "$REPO_ROOT/.venv"; then
            _install_line "  [2/5] deps        ✗ failed to recreate .venv"
            INSTALL_HARD_FAIL=1; return
        fi
    fi
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.venv/bin/activate"
    if pip install -q -e ".[${LAB_TIER}]" 2>&1 | tail -20 1>&2; then
        _install_line "  [2/5] deps        ✓ pip install -e \".[${LAB_TIER}]\""
    else
        _install_line "  [2/5] deps        ✗ pip install failed — see above"
        INSTALL_HARD_FAIL=1
    fi
}

# ── [3/5] components ─────────────────────────────────────────────────────
_install_components_phase() {
    local args rc=0
    if [[ "$CHECK_MODE" == "1" ]]; then
        args=(--check --non-interactive)
    else
        args=(--apply --non-interactive)
    fi
    [[ "$FORCE" == "1" ]] && args+=(--force)
    if [[ "$JSON_MODE" == "1" ]]; then
        bash "$REPO_ROOT/scripts/update.sh" "${args[@]}" 1>&2 || rc=$?
    else
        bash "$REPO_ROOT/scripts/update.sh" "${args[@]}" || rc=$?
    fi
    if [[ "$CHECK_MODE" == "1" ]]; then
        case "$rc" in
            0) _install_line "  [3/5] components  ✓ up to date" ;;
            3) _install_line "  [3/5] components  ⚠ update(s) pending"; PHASE_DEGRADED=1 ;;
            *) _install_line "  [3/5] components  ⚠ check did not complete (exit $rc)"; PHASE_DEGRADED=1 ;;
        esac
        return
    fi
    case "$rc" in
        0) _install_line "  [3/5] components  ✓ up to date" ;;
        3) _install_line "  [3/5] components  ⚠ one or more components failed — see above"; PHASE_DEGRADED=1 ;;
        *) _install_line "  [3/5] components  ⚠ did not complete cleanly (exit $rc) — see above"; PHASE_DEGRADED=1 ;;
    esac
    # Never: fail the whole run for one optional component (§6.3's phase
    # table) — components-phase problems always degrade, never hard-fail.
}

# ── [4/5] models ─────────────────────────────────────────────────────────
_install_expected_model() {
    if [[ -f "$REPO_ROOT/model_defaults.yaml" ]]; then
        local v
        v="$(python3 -c '
import sys
try:
    import yaml
except Exception:
    sys.exit(0)
try:
    data = yaml.safe_load(open(sys.argv[1]).read()) or {}
except Exception:
    sys.exit(0)
v = None
if isinstance(data, dict):
    # v2: the last-resolved fast model, else the pin; v1: default_a.
    resolved_a = ((data.get("resolved") or {}).get("a") or {}).get("model")
    pins = data.get("pins") if isinstance(data.get("pins"), dict) else {}
    v = resolved_a or pins.get("a") or data.get("default_a")
if v:
    print(v)
' "$REPO_ROOT/model_defaults.yaml" 2>/dev/null)"
        [[ -n "$v" ]] && { printf '%s' "$v"; return; }
    fi
    printf 'llama-ai-eng'
}

_install_models_phase() {
    if ! command -v ollama >/dev/null 2>&1; then
        _install_line "  [4/5] models      ⚠ skipped — ollama not installed"
        return
    fi
    local list_out
    declare -F inst_load_setup_functions >/dev/null 2>&1 && inst_load_setup_functions _arail_timeout >/dev/null 2>&1
    if declare -F _arail_timeout >/dev/null 2>&1; then
        list_out="$(_arail_timeout 5 ollama list 2>/dev/null)" || { _install_line "  [4/5] models      ⚠ skipped — ollama daemon unreachable"; return; }
    else
        list_out="$(ollama list 2>/dev/null)" || { _install_line "  [4/5] models      ⚠ skipped — ollama daemon unreachable"; return; }
    fi
    local expected
    expected="$(_install_expected_model)"
    if printf '%s\n' "$list_out" | awk 'NR>1{print $1}' | grep -qx -- "${expected}" \
        || printf '%s\n' "$list_out" | awk 'NR>1{print $1}' | grep -qx -- "${expected}:latest"; then
        _install_line "  [4/5] models      ✓ ${expected} installed"
        return
    fi
    if [[ "$WANT_MODELS" != "1" ]]; then
        if [[ "$expected" == "llama-ai-eng" ]]; then
            _install_line "  [4/5] models      ⚠ ${expected} not installed — run with --models, or manually:"
            _install_line "                    ollama pull llama3.2:1b && ollama create llama-ai-eng -f models/ai-eng/Modelfile.default"
        else
            _install_line "  [4/5] models      ⚠ ${expected} not installed — run with --models, or manually: ollama pull ${expected}"
        fi
        PHASE_DEGRADED=1; return
    fi
    if [[ "$AIRGAPPED_BLOCKED" == "1" ]]; then
        _install_line "  [4/5] models      ⚠ ${expected} not installed — refused: LAB_MODE=airgapped (use --force to override)"
        PHASE_DEGRADED=1; return
    fi
    if [[ "$expected" == "llama-ai-eng" ]]; then
        local pull_ok=1
        if declare -F _arail_timeout >/dev/null 2>&1; then
            _arail_timeout 180 ollama pull llama3.2:1b || pull_ok=0
        else
            ollama pull llama3.2:1b || pull_ok=0
        fi
        if [[ "$pull_ok" == "1" ]] && ollama create llama-ai-eng -f "$REPO_ROOT/models/ai-eng/Modelfile.default" >/dev/null 2>&1; then
            _install_line "  [4/5] models      ✓ ${expected} installed"
        else
            _install_line "  [4/5] models      ✗ ${expected} install failed — see above"
            PHASE_DEGRADED=1
        fi
    else
        local pull_ok=1
        if declare -F _arail_timeout >/dev/null 2>&1; then
            _arail_timeout 180 ollama pull "$expected" || pull_ok=0
        else
            ollama pull "$expected" || pull_ok=0
        fi
        if [[ "$pull_ok" == "1" ]]; then
            _install_line "  [4/5] models      ✓ ${expected} installed"
        else
            _install_line "  [4/5] models      ✗ ollama pull ${expected} failed"
            PHASE_DEGRADED=1
        fi
    fi
}

# ── Compiled-KB bootstrap (QA-6) ────────────────────────────────────────
# One non-fatal backfill call so a freshly-installed/updated lab's Compiled
# KB manifest exists (state moves from "unbootstrapped" to "empty"/
# "populated") without the operator needing to know the verb exists.
# Never on `start` — only here and via the explicit `./arailctl pkb
# bootstrap` verb. A failure here degrades, never hard-fails, install.
_install_kb_bootstrap() {
    [[ -d "$REPO_ROOT/.venv" ]] || return 0
    local out rc=0
    out="$(cd "$REPO_ROOT" && source .venv/bin/activate && python -m arail.compiled_kb bootstrap 2>&1)" || rc=$?
    if [[ "$rc" == "0" ]]; then
        _install_line "  [kb]              $(echo "$out" | tr '\n' ' ')"
    else
        _install_line "  [kb]              ⚠ Compiled KB bootstrap skipped (exit $rc)"
    fi
}

# ── Relational store: ensure every resolved root's arail.db is created
# and forward-migrated (sprints/2026-08-10-arail2-persistence-instantiated
# §4.6 — "the seamless promise's real home"). SAFE-FORWARD only, applied
# per root; LOSSY/AHEAD/DIVERGED are reported, never applied, per root.
# One line per root; a healthy already-ok root prints nothing (quiet
# boot). Never fatal — a DB problem degrades install, it doesn't hard-fail
# it (nothing reads arail.db at runtime yet, per that sprint's §0).
_install_db_ensure() {
    [[ -d "$REPO_ROOT/.venv" ]] || return 0
    local slug data_dir origin line rc
    while IFS=$'\t' read -r slug data_dir origin; do
        [[ -n "$data_dir" ]] || continue
        line="$(cd "$REPO_ROOT" && source .venv/bin/activate && \
            python -m arail.dbspec.ensure "$data_dir" --apply --quiet-ok 2>&1)"
        rc=$?
        if [[ -n "$line" ]]; then
            _install_line "  [db]              ${line}"
        fi
        [[ "$rc" != "0" ]] && PHASE_DEGRADED=1
    done < <(inst_resolve_data_dirs)
    return 0
}

# ── [5/5] verify ─────────────────────────────────────────────────────────
_install_verify_phase() {
    local rc=0
    _install_db_ensure
    _install_kb_bootstrap
    if [[ "$JSON_MODE" == "1" ]]; then
        bash "$REPO_ROOT/arailctl" doctor 1>&2 || rc=$?
    else
        bash "$REPO_ROOT/arailctl" doctor || rc=$?
    fi
    case "$rc" in
        0) _install_line "  [5/5] verify      ✓ doctor: healthy" ;;
        3) _install_line "  [5/5] verify      ⚠ doctor: degraded"; PHASE_DEGRADED=1 ;;
        1) _install_line "  [5/5] verify      ✗ doctor: broken"; INSTALL_HARD_FAIL=1 ;;
        *) _install_line "  [5/5] verify      ⚠ doctor: unexpected exit $rc"; PHASE_DEGRADED=1 ;;
    esac
}

# ── Run ───────────────────────────────────────────────────────────────────
_install_header="${BOLD}install${RESET}"
[[ "$CHECK_MODE" == "1" ]] && _install_header="${_install_header} (--check)"
_install_line ""
_install_line "$_install_header"
_install_line ""

for _install_p in source deps components models verify; do
    _install_phase_enabled "$_install_p" || continue
    case "$_install_p" in
        source)     _install_source_phase ;;
        deps)       _install_deps_phase ;;
        components) _install_components_phase ;;
        models)     _install_models_phase ;;
        verify)     _install_verify_phase ;;
    esac
    [[ "$INSTALL_HARD_FAIL" == "1" ]] && break
done
unset _install_p

_install_line ""
if [[ "$INSTALL_HARD_FAIL" == "1" ]]; then
    VERDICT_CODE=1
    VERDICT_STATE="error"
    _install_line "install: hard failure — see above."
elif [[ "$PHASE_DEGRADED" == "1" ]]; then
    VERDICT_CODE=3
    VERDICT_STATE="degraded"
    _install_line "install: degraded — see above."
else
    VERDICT_CODE=0
    VERDICT_STATE="ok"
    _install_line "install: ok."
fi

if [[ "$JSON_MODE" == "1" ]]; then
    python3 -c '
import json, sys
print(json.dumps({
    "schema": "arail.install/v1",
    "check": sys.argv[1] == "1",
    "verdict": {"code": int(sys.argv[2]), "state": sys.argv[3]},
}))
' "$CHECK_MODE" "$VERDICT_CODE" "$VERDICT_STATE"
fi

exit "$VERDICT_CODE"

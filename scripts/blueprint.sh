#!/usr/bin/env bash
# blueprint.sh — implementation of `./arailctl blueprint <subcommand>`.
#
# Subcommands:
#   list                                       Show available blueprints.
#   catalog                                    Show the model compatibility matrix.
#   show <id>                                  Print one blueprint.
#   create <instance> --from <id> [opts]       Scaffold a new instance.
#   apply <instance>                           Re-render an instance's .env + lab.conf.
#   destroy <instance>                         Remove an instance.
#   help                                       This message.
#
# Multi-instance design:
#   The default ARAIL lab — what `./arailctl setup` provisions — stays
#   at the repo root (./.env, ./lab.conf). New instances scaffolded
#   by `create` live under ./instances/<name>/. Nothing migrates;
#   nothing breaks.
#
# NOT the same thing as lab/instances/ — repo-root ./instances/ here is a
# config-only scaffold (.env/lab.conf/blueprint.toml); nothing under
# src/arail/ reads it, and `create` never spawns a running process.
# lab/instances/ (sprints/2026-07-28-concurrent-worlds/, see
# docs/concurrent-worlds.md) is the REAL runtime home for a concurrently-
# running World instance (registry, per-instance data/pkb/env-pack). Two
# directories named "instances" meaning different things is a known,
# tracked point of confusion — see sprints/BACKLOG.md "Unify blueprint
# instances with runtime instances (+ decide ARAIL_HOME)".

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BLUEPRINTS_DIR="$REPO_ROOT/blueprints"
INSTANCES_DIR="$REPO_ROOT/instances"
CATALOG_FILE="$REPO_ROOT/catalog/models.toml"
DEFAULT_PORT_BASE=9100

BOLD="\033[1m"; GREEN="\033[0;32m"; CYAN="\033[0;36m"; YELLOW="\033[0;33m"; RED="\033[0;31m"; RESET="\033[0m"
say()  { echo -e "${GREEN}[blueprint]${RESET} $*"; }
warn() { echo -e "${YELLOW}[blueprint]${RESET} $*"; }
die()  { echo -e "${RED}[blueprint]${RESET} $*" >&2; exit 1; }

require_python_311() {
    if ! python3 -c "import tomllib" 2>/dev/null; then
        die "Python 3.11+ with tomllib required (this script uses tomllib for parsing)."
    fi
}

# ── list ────────────────────────────────────────────────────────────
cmd_list() {
    require_python_311
    [[ -d "$BLUEPRINTS_DIR" ]] || die "no blueprints/ directory at $BLUEPRINTS_DIR"
    python3 - "$BLUEPRINTS_DIR" <<'PY'
import sys, tomllib, pathlib
root = pathlib.Path(sys.argv[1])
rows = []
for bp in sorted(root.glob("*/blueprint.toml")):
    with bp.open("rb") as fh:
        d = tomllib.load(fh)
    rows.append((d.get("id", bp.parent.name), d.get("label", "?"), d.get("tier", "?"), d.get("description", "").strip().splitlines()[0] if d.get("description") else ""))
if not rows:
    print("(no blueprints found)")
    sys.exit(0)
print(f"{'ID':<20} {'LABEL':<24} {'TIER':<6} DESCRIPTION")
print("-" * 100)
for rid, label, tier, desc in rows:
    print(f"{rid:<20} {label:<24} {tier:<6} {desc[:60]}")
PY
}

# ── catalog ─────────────────────────────────────────────────────────
cmd_catalog() {
    require_python_311
    [[ -f "$CATALOG_FILE" ]] || die "catalog file not found at $CATALOG_FILE"
    python3 - "$CATALOG_FILE" <<'PY'
import sys, tomllib, pathlib
with open(sys.argv[1], "rb") as fh:
    data = tomllib.load(fh)
models = data.get("model", [])
if not models:
    print("(catalog is empty)")
    sys.exit(0)
print(f"{'ID':<28} {'LABEL':<32} {'TIER':<9} {'QueueLLM':<11} {'AirLLM':<11} {'MLX':<13}")
print("-" * 110)
for m in models:
    print(f"{m['id']:<28} {m['label']:<32} {m['lab_tier']:<9} "
          f"{m.get('aerollm_status','?'):<11} {m.get('airllm_status','?'):<11} "
          f"{m.get('mlx_status','?'):<13}")
PY
}

# ── show ────────────────────────────────────────────────────────────
cmd_show() {
    local id="${1:-}"
    [[ -n "$id" ]] || die "Usage: ./arailctl blueprint show <id>"
    local path="$BLUEPRINTS_DIR/$id/blueprint.toml"
    [[ -f "$path" ]] || die "blueprint not found: $id (try: ./arailctl blueprint list)"
    echo "── $path ──"
    cat "$path"
}

# ── create ──────────────────────────────────────────────────────────
allocate_port_base() {
    # Find the next free port base. Strategy: scan existing
    # instances/<name>/lab.conf for PORTAL_PORT, take max + 10, or
    # DEFAULT_PORT_BASE if no instances yet.
    local max=0
    if [[ -d "$INSTANCES_DIR" ]]; then
        for conf in "$INSTANCES_DIR"/*/lab.conf; do
            [[ -f "$conf" ]] || continue
            local p
            p="$(grep -E '^PORTAL_PORT=' "$conf" | head -1 | cut -d= -f2 || echo 0)"
            (( p > max )) && max=$p
        done
    fi
    if (( max == 0 )); then
        echo "$DEFAULT_PORT_BASE"
    else
        echo "$(( max + 10 ))"
    fi
}

cmd_create() {
    require_python_311
    local instance="" blueprint_id="" port_base=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --from)        blueprint_id="$2"; shift 2 ;;
            --from=*)      blueprint_id="${1#--from=}"; shift ;;
            --port-base)   port_base="$2"; shift 2 ;;
            --port-base=*) port_base="${1#--port-base=}"; shift ;;
            -*)            die "unknown flag: $1" ;;
            *)             [[ -z "$instance" ]] && instance="$1" || die "extra arg: $1"; shift ;;
        esac
    done

    [[ -n "$instance" ]] || die "Usage: ./arailctl blueprint create <instance> --from <blueprint-id>"
    [[ -n "$blueprint_id" ]] || die "missing --from <blueprint-id>"
    [[ "$instance" =~ ^[a-z][a-z0-9-]*$ ]] || die "instance name must match [a-z][a-z0-9-]* (got: $instance)"

    local bp_path="$BLUEPRINTS_DIR/$blueprint_id/blueprint.toml"
    [[ -f "$bp_path" ]] || die "blueprint not found: $blueprint_id (try: ./arailctl blueprint list)"

    local idir="$INSTANCES_DIR/$instance"
    [[ ! -d "$idir" ]] || die "instance already exists: $idir (try: ./arailctl blueprint apply $instance)"

    [[ -n "$port_base" ]] || port_base="$(allocate_port_base)"
    [[ "$port_base" =~ ^[0-9]+$ ]] || die "port-base must be numeric (got: $port_base)"

    say "Creating instance '$instance' from blueprint '$blueprint_id' at port-base $port_base"
    mkdir -p "$idir"
    cp "$bp_path" "$idir/blueprint.toml"
    _render_instance "$instance" "$port_base"
    say "Instance scaffolded:"
    say "  $idir/blueprint.toml   (snapshot of source blueprint)"
    say "  $idir/.env             (per-instance env)"
    say "  $idir/lab.conf         (per-instance ports)"
    say "  $idir/log/             (telemetry sink directory)"
    say ""
    say "Next: ./arailctl blueprint apply $instance   (re-render after editing)"
}

# ── apply ───────────────────────────────────────────────────────────
cmd_apply() {
    require_python_311
    local instance="${1:-}"
    [[ -n "$instance" ]] || die "Usage: ./arailctl blueprint apply <instance>"
    local idir="$INSTANCES_DIR/$instance"
    [[ -d "$idir" ]] || die "instance not found: $instance (try: ./arailctl blueprint create $instance --from <id>)"
    local conf="$idir/lab.conf"
    local port_base
    if [[ -f "$conf" ]]; then
        port_base="$(grep -E '^PORTAL_PORT=' "$conf" | head -1 | cut -d= -f2)"
    fi
    [[ -n "${port_base:-}" ]] || port_base="$(allocate_port_base)"
    say "Applying instance '$instance' (port-base $port_base)"
    _render_instance "$instance" "$port_base"
    say "Done. Inspect: cat $idir/.env $idir/lab.conf"
}

# ── destroy ─────────────────────────────────────────────────────────
cmd_destroy() {
    local instance="${1:-}"
    [[ -n "$instance" ]] || die "Usage: ./arailctl blueprint destroy <instance>"
    local idir="$INSTANCES_DIR/$instance"
    [[ -d "$idir" ]] || die "instance not found: $instance"
    warn "About to remove: $idir"
    read -r -p "  Type the instance name to confirm: " reply
    [[ "$reply" == "$instance" ]] || die "name mismatch — aborted"
    rm -rf "$idir"
    say "Instance '$instance' destroyed."
    say "(default lab at repo root, blueprints/, and catalog/ untouched)"
}

# ── render helper ───────────────────────────────────────────────────
# Reads instances/<name>/blueprint.toml + a port_base, writes
# instances/<name>/.env and instances/<name>/lab.conf.
_render_instance() {
    local instance="$1"
    local port_base="$2"
    local idir="$INSTANCES_DIR/$instance"
    mkdir -p "$idir/log"

    # Validate model references against the catalog.
    python3 - "$idir/blueprint.toml" "$CATALOG_FILE" <<'PY'
import sys, tomllib, pathlib
bp_path, cat_path = sys.argv[1:3]
with open(bp_path, "rb") as fh:
    bp = tomllib.load(fh)
with open(cat_path, "rb") as fh:
    cat = tomllib.load(fh)
catalog_ids = {m["id"] for m in cat.get("model", [])}
problems = []
for slot, mid in (bp.get("runtime", {}).get("models", {}) or {}).items():
    if mid not in catalog_ids:
        problems.append(f"runtime.models.{slot} = '{mid}' not in catalog")
if problems:
    print("Blueprint validation failed:", file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    sys.exit(2)
PY

    python3 - "$idir" "$instance" "$port_base" <<'PY'
import sys, tomllib, pathlib
idir = pathlib.Path(sys.argv[1])
instance, port_base = sys.argv[2], int(sys.argv[3])
with (idir / "blueprint.toml").open("rb") as fh:
    bp = tomllib.load(fh)
ports = bp.get("ports", {})
def p(name, default_offset):
    return port_base + ports.get(name, default_offset)

import re as _re
# The instance .env is shell-sourced (set -a; source .env). Free-text fields
# (label/description/goal_prompt) must be quoted AND escaped, or a value with a
# space breaks `source` and a $/backtick expands or executes. Mirrors
# scripts/setup.sh _set_env_var and src/arail/env_writer._quote_for_env.
_ENV_SAFE = _re.compile(r"\A[A-Za-z0-9_./:@%+,-]+\Z")
def shq(v: str) -> str:
    if _ENV_SAFE.match(v):
        return v
    esc = v.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return f'"{esc}"'

lab_name = f'{bp.get("label", instance)} ({instance})'
tagline = bp.get("description", "").strip().splitlines()[0] if bp.get("description") else ""
env_lines = [
    f"# {instance} — generated by ./arailctl blueprint apply {instance}",
    f"# Source blueprint: {bp.get('id', '?')} ({bp.get('label', '?')})",
    f"# Edit blueprint.toml or this file directly; re-running apply preserves --port-base.",
    "",
    f"LAB_NAME={shq(lab_name)}",
    f"LAB_SHORT_NAME={shq(instance)}",
    f"LAB_TAGLINE={shq(tagline)}",
    f"LAB_TIER={shq(bp.get('tier', 'min'))}",
    f"LAB_INTENT={shq(bp.get('goal_prompt', ''))}",
    "",
]
(idir / ".env").write_text("\n".join(env_lines) + "\n")

conf_lines = [
    f"# {instance} — generated by ./arailctl blueprint apply {instance}",
    f"# Port base: {port_base}. To change, edit and re-apply.",
    "",
    f"PORTAL_PORT={p('portal', 0)}",
    f"TERMINAL_PORT={p('terminal', 1)}",
    f"NOTEBOOK_PORT={p('notebook', 2)}",
    f"IDE_PORT={p('ide', 3)}",
    f"MLX_OPENAI_PORT={p('mlx_openai', 5)}",
    f"BIND_ADDR=127.0.0.1",
]
(idir / "lab.conf").write_text("\n".join(conf_lines) + "\n")
PY

    # Telemetry sink directories — best-effort; sinks are documented
    # intent today (see docs/CaC.md or blueprints/README.md).
    python3 - "$idir" <<'PY'
import sys, tomllib, pathlib
idir = pathlib.Path(sys.argv[1])
with (idir / "blueprint.toml").open("rb") as fh:
    bp = tomllib.load(fh)
sink_dir = idir / bp.get("telemetry", {}).get("sink_dir", "log")
sink_dir.mkdir(exist_ok=True, parents=True)
PY
}

# ── help ────────────────────────────────────────────────────────────
cmd_help() {
    cat <<'EOF'

  ./arailctl blueprint <subcommand>

  Subcommands:
    list                                Show available blueprints.
    catalog                             Show the model compatibility matrix.
    show <id>                           Print one blueprint's TOML.
    create <name> --from <id>           Scaffold a new instance from a blueprint.
                  [--port-base N]       Override auto-allocated port base.
    apply <name>                        Re-render an instance's .env + lab.conf.
    destroy <name>                      Remove an instance (interactive confirm).
    help                                This message.

  Examples:
    ./arailctl blueprint list
    ./arailctl blueprint catalog
    ./arailctl blueprint create research --from autoresearch
    ./arailctl blueprint apply research
    ./arailctl blueprint destroy research

  Multi-instance: each blueprint create scaffolds instances/<name>/
  with its own .env + lab.conf + log dir. The default lab at the
  repo root is untouched. See blueprints/README.md for the full
  authoring guide.

EOF
}

# ── Dispatch ────────────────────────────────────────────────────────
SUBCMD="${1:-help}"
shift || true
case "$SUBCMD" in
    list)             cmd_list "$@" ;;
    catalog)          cmd_catalog "$@" ;;
    show)             cmd_show "$@" ;;
    create)           cmd_create "$@" ;;
    apply)            cmd_apply "$@" ;;
    destroy)          cmd_destroy "$@" ;;
    help|-h|--help|"") cmd_help ;;
    *) cmd_help; die "unknown blueprint subcommand: $SUBCMD" ;;
esac

#!/usr/bin/env bash
# tests/cli/lib.sh — shared harness for the tests/cli/*_driver.sh scripts
# (sprints/2026-07-29-elite-cli/ARCHITECTURE.md §16.1).
#
# Modeled directly on tests/instance_start_driver.sh: real scripts/ copied
# into a throwaway repo, a controllable venv, stub PATH, isolated HOME (so
# a developer's real launchd plist/lab can never leak in), and a portable
# `_timeout` (macOS ships none).
#
# F26/F27 (load-bearing, not just isolation): every port this harness uses
# is randomized >= 18000 and is NEVER 8080/8090. reset.sh's pre-upgrade
# fallback stop pattern matches on argv WITHOUT --app-dir; on the wrong
# port it could reach a developer's real running lab. Every driver that
# sources this file MUST call `cli_test_assert_port_safe` on any port it
# is about to use before touching it.
set -uo pipefail

CLI_TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_TEST_REPO="$(cd "$CLI_TEST_DIR/../.." && pwd)"

_cli_test_find_venv() {
    local c
    for c in "${ARAIL_TEST_VENV:-}" "$CLI_TEST_REPO/.venv" "$CLI_TEST_REPO/../.venv"; do
        [[ -n "$c" && -x "$c/bin/python" ]] && { printf '%s' "$c"; return 0; }
    done
    return 1
}

REAL_VENV="$(_cli_test_find_venv)" || REAL_VENV=""

# QA-8 (sprints/2026-08-10-arail2-persistence-instantiated/TEST_REPORT.md):
# pin the interpreter's `arail` to THE CHECKOUT UNDER TEST.
#
# make_fake_venv symlinks the discovered venv's site-packages into every fake
# repo. In CI that venv lives in the same checkout (`pip install -e .`) and
# resolves correctly. In a *worktree* — the way every round of this sprint was
# reviewed — REAL_VENV is the operator's main checkout's .venv, whose editable
# `__editable__.arail-1.0.0.pth` points at THAT checkout's src/. A driver run
# from a worktree therefore exercised main's `arail`, not the branch's:
# measured 2026-08-10, `status_driver.sh` failed T10 with
# `ModuleNotFoundError: No module named 'arail.dbspec.ensure'` (a module that
# exists only on this branch), and passed 16/16 the moment PYTHONPATH pointed
# at the worktree's src. Without this line, a shell driver can report green
# while testing entirely different source — the exact class of blindness this
# layer was already accused of twice.
#
# Prepended, so a driver that deliberately shadows a module (see
# qa_db_ledger_driver.sh) still wins by prepending its own entry.
export PYTHONPATH="$CLI_TEST_REPO/src${PYTHONPATH:+:$PYTHONPATH}"

# Portable timeout wrapper (macOS ships no timeout(1)) — kills the WHOLE
# process group on expiry so no stub server/uvicorn ever survives a driver
# run. Requires REAL_VENV (python3).
_timeout() {
    local secs="$1"; shift
    "$REAL_VENV/bin/python" -c '
import os, signal, subprocess, sys
secs = float(sys.argv[1])
cmd = sys.argv[2:]
p = subprocess.Popen(cmd, start_new_session=True)
try:
    sys.exit(p.wait(timeout=secs))
except subprocess.TimeoutExpired:
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except Exception:
        pass
    sys.exit(124)
' "$secs" "$@"
}

# F26: a random high port, never the two real lab ports.
cli_test_random_port() {
    local p=$(( 18000 + RANDOM % 20000 ))
    while [[ "$p" == "8080" || "$p" == "8090" ]]; do
        p=$(( p + 1 ))
    done
    printf '%s' "$p"
}

# F27: hard refusal if a driver ever computes 8080/8090 as a working port —
# stated in this file's own header as a load-bearing safety property, not
# just isolation, per ARCHITECTURE.md F27.
cli_test_assert_port_safe() {
    local p="$1"
    case "$p" in
        8080|8090)
            echo "REFUSING to run: port $p is a real ARAIL lab port (F27) — a bug in this harness would risk killing a developer's real running lab" >&2
            exit 1
            ;;
    esac
}

# ── Fake repo: real scripts/ + arailctl, throwaway lab/, isolated env ───
# make_fake_repo <dest-dir>
make_fake_repo() {
    local fake="$1"
    mkdir -p "$fake"
    cp -R "$CLI_TEST_REPO/scripts" "$fake/scripts"
    cp "$CLI_TEST_REPO/arailctl" "$fake/arailctl"
    mkdir -p "$fake/lab/worlds" "$fake/lab/models" "$fake/lab/data" "$fake/lab/logs"
    printf 'LAB_NAME="Test Lab"\nLAB_SHORT_NAME=test-lab\n' > "$fake/.env"
    printf '%s' "$fake"
}

# cli_test_make_world <fake-repo> <slug> <display-name> — a real, seal-valid
# World bundle under <fake-repo>/lab/worlds/<slug>, built via the SAME
# fixture builder tests/instance_start_driver.sh already uses
# (tests/world_bundle_builder.py) rather than a second, hand-rolled bundle
# layout. Requires REAL_VENV (world_mount's seal check needs `arail.*`).
cli_test_make_world() {
    local fake="$1" slug="$2" name="$3"
    [[ -n "$REAL_VENV" ]] || return 1
    PYTHONPATH="$CLI_TEST_REPO/tests" "$REAL_VENV/bin/python" -c "
from pathlib import Path
from world_bundle_builder import make_bundle
make_bundle(Path('$fake/lab/worlds'), slug='$slug', display_name='$name')
"
}

# cli_test_fabricate_live_instance <fake-repo> <slug> — registers a
# registry/v1 record for <slug> backed by a REAL long-lived `sleep`
# process (standing in for portal_pid/memory_pid/launcher_pid alike, the
# same trick tests/instance_start_driver.sh's ceiling scenario already
# uses) so inst_alive() reports it live WITHOUT running the full 8-stage
# boot. Sets CLI_TEST_LAST_FABRICATED_PID — the caller reads it right
# after the call and kills it during cleanup if reset.sh's own stop path
# didn't already reap it. Deliberately NOT `pid=$(cli_test_fabricate...)`:
# a background job started inside a command-substitution subshell does
# not survive that subshell's own exit here (confirmed empirically while
# building this driver — the sleep died within ~0.3s, silently, exactly
# the failure mode a "gap-3 regression" scenario cannot afford to have as
# a FALSE negative). Same class of lesson root_start_driver.sh's
# _new_scenario() already documents for plain variable globals — this is
# the process-lifetime version of it.
# Requires a stub `ps` on PATH that returns a cmdline containing
# "uvicorn.*arail.portal.app", "--port <port>", "scripts/start.sh", and
# "--world <slug>" for whatever pid is queried (see
# cli_test_write_stub_ps_for_slugs, below) — this function does not write
# that stub itself, since a scenario with multiple fabricated instances
# needs exactly ONE stub answering for all of them.
cli_test_fabricate_live_instance() {
    local fake="$1" slug="$2" port="$3"
    mkdir -p "$fake/lab/instances/registry.d"
    sleep 30 &
    CLI_TEST_LAST_FABRICATED_PID=$!
    local pid="$CLI_TEST_LAST_FABRICATED_PID"
    cat > "$fake/lab/instances/registry.d/${slug}.json" <<EOF
{"schema":"arail.instance-registry/v1","slug":"${slug}","display_name":"${slug}",
 "checkout":"$fake","instance_root":"$fake/lab/instances/${slug}",
 "data_dir":"$fake/lab/instances/${slug}/data","pkb_root":"$fake/lab/instances/${slug}/pkb",
 "bind":"127.0.0.1","portal_port":${port},"lance_port":$((port + 4)),
 "launcher_pid":${pid},"portal_pid":${pid},"memory_pid":${pid},"token":"t",
 "started_at":"2026-01-01T00:00:00Z","arailctl_version":"test"}
EOF
}

# cli_test_fabricate_live_instance_portal_like <fake-repo> <slug> <port> —
# REVIEW.md B2: registers a registry/v1 record for <slug> backed by a REAL,
# long-lived process whose OWN argv is INDISTINGUISHABLE from a genuine
# World-instance portal — "uvicorn arail.portal.app:app --host 127.0.0.1
# --port <port> --log-level warning", deliberately with NO --app-dir
# (start.sh's instance path never adds one — B2's exact shape).
#
# Unlike cli_test_fabricate_live_instance (a bare `sleep`, whose real argv
# is just "sleep 30"), this fixture is needed because reset.sh's
# stop_services() finds its fallback kill candidates via a REAL `pgrep -f`
# — a kernel-level process-table query that no stubbed `ps` on PATH can
# influence. So both `pgrep -f` AND `ps -p <pid> -o command=` (which
# inst_alive() also calls) must see the SAME real, matching argv — achieved
# here by literally naming the stub executable "uvicorn" and invoking it
# with the exact instance-portal argv shape; no `ps` stub is needed at all
# for THIS fixture (unlike cli_test_fabricate_live_instance's sibling
# scenarios), since the real system `ps` already reports the right thing.
# Sets CLI_TEST_LAST_FABRICATED_PID (same global-handoff convention as
# cli_test_fabricate_live_instance, for the identical subshell-swallows-a-
# background-job reason — NOT `pid=$(...)`).
cli_test_fabricate_live_instance_portal_like() {
    local fake="$1" slug="$2" port="$3"
    mkdir -p "$fake/lab/instances/registry.d" "$fake/fake-world-portal"
    cat > "$fake/fake-world-portal/uvicorn" <<'EOF'
#!/usr/bin/env bash
# `trap 'exit 0' TERM INT` alone (no child cleanup) ORPHANS the `sleep 300`
# child on SIGTERM — found the hard way via a pytest wrapper
# (tests/test_cli_restart.py, no _timeout process-group safety net,
# unlike every *_driver.sh script's own `_timeout` calls) that hung for a
# full 180s: subprocess.communicate() cannot see EOF on stdout/stderr
# until every process holding those inherited fds exits, and an
# orphaned, un-reaped `sleep 300` holds them for up to 300s. Kill the
# child explicitly before exiting, same as write_stub_uvicorn_serving's
# wrapper already does.
_fabricated_child=""
trap 'kill "$_fabricated_child" 2>/dev/null; wait "$_fabricated_child" 2>/dev/null; exit 0' TERM INT
sleep 300 &
_fabricated_child=$!
wait
EOF
    chmod +x "$fake/fake-world-portal/uvicorn"
    "$fake/fake-world-portal/uvicorn" arail.portal.app:app --host 127.0.0.1 --port "$port" --log-level warning &
    CLI_TEST_LAST_FABRICATED_PID=$!
    local pid="$CLI_TEST_LAST_FABRICATED_PID"
    cat > "$fake/lab/instances/registry.d/${slug}.json" <<EOF
{"schema":"arail.instance-registry/v1","slug":"${slug}","display_name":"${slug}",
 "checkout":"$fake","instance_root":"$fake/lab/instances/${slug}",
 "data_dir":"$fake/lab/instances/${slug}/data","pkb_root":"$fake/lab/instances/${slug}/pkb",
 "bind":"127.0.0.1","portal_port":${port},"lance_port":$((port + 4)),
 "launcher_pid":${pid},"portal_pid":${pid},"memory_pid":${pid},"token":"t",
 "started_at":"2026-01-01T00:00:00Z","arailctl_version":"test"}
EOF
}

# cli_test_write_stub_ps_for_slugs <bindir> <slug1>:<port1> [<slug2>:<port2> ...]
# — a `ps` stub whose cmdline output contains every given "--port <n>" AND
# "--world <slug>" needed to satisfy stop_instance()'s (scripts/reset.sh)
# per-field substring verification, regardless of which pid was actually
# queried (this stub ignores its own -p argument entirely, same shape as
# tests/instance_start_driver.sh's ceiling-scenario `ps` stub — a single
# fixed cmdline satisfying multiple records' checks via substring matches).
cli_test_write_stub_ps_for_slugs() {
    local bin="$1"; shift
    mkdir -p "$bin"
    local line="python -m uvicorn arail.portal.app:app scripts/start.sh"
    local pair slug port
    for pair in "$@"; do
        slug="${pair%%:*}"; port="${pair##*:}"
        line="${line} --port ${port} --world ${slug}"
    done
    cat > "$bin/ps" <<EOF
#!/usr/bin/env bash
echo "${line}"
EOF
    chmod +x "$bin/ps"
}

# ── Fake venv: a REAL, working python (site-packages symlinked from the
# real venv, so `import arail` works) but a `uvicorn` this harness fully
# controls. Needed because `source .venv/bin/activate` (start.sh, and
# arailctl's `doctor`/`wiki`/etc. cases) always PREPENDS $VIRTUAL_ENV/bin
# ahead of anything already on PATH — a stub `uvicorn` prepended onto PATH
# before invoking start.sh would otherwise be shadowed by the real venv's
# own (fully working) uvicorn the moment activate runs. Symlinking
# individual files (not the whole venv dir, unlike
# tests/instance_start_driver.sh's simpler needs) is what makes overriding
# just uvicorn possible while everything else keeps working.
#
# ALSO note: a real venv's `bin/activate` bakes an ABSOLUTE VIRTUAL_ENV
# path in at creation time (this repo's own is a hardcoded path) — reusing
# it verbatim would point PATH back at the REAL venv/bin, defeating this
# function's whole purpose. A minimal from-scratch activate script is
# written instead.
make_fake_venv() {
    local fake="$1"
    [[ -n "$REAL_VENV" ]] || return 1
    mkdir -p "$fake/.venv/bin"
    # ⚠️  DANGER — READ BEFORE WRITING A NEW SCENARIO OR EDITING THIS LINE.
    # "$fake/.venv/lib" is a SYMLINK straight into the REAL venv's
    # site-packages (the whole `lib/` dir, not a copy). Any scenario that
    # renames, edits, deletes, or writes through a path reached via
    # "$fake/.venv/lib/..." — e.g. "rename ensure.py so an import fails,"
    # "patch a module file to simulate a bug" — mutates the OPERATOR'S
    # REAL INSTALLATION, not a fixture. This is not hypothetical: a draft
    # of a status_driver.sh scenario did exactly this during
    # sprints/2026-08-10-arail2-persistence-instantiated's round 2 (caught
    # and discarded before it ran — see REVIEW2.md's "make_fake_venv
    # footgun" section). To simulate an import/module failure, break it
    # from the CALLER side instead — a PYTHONPATH that shadows the target
    # module ahead of site-packages, or a stub `python3` earlier on PATH
    # — never by touching anything under this symlink. If you need a
    # scenario to write into "fake"'s own site-packages specifically,
    # this helper is the wrong tool; per-package symlinks or a read-only
    # tree are the follow-up filed in sprints/BACKLOG.md, not yet built.
    ln -s "$REAL_VENV/lib" "$fake/.venv/lib" 2>/dev/null || true
    [[ -f "$REAL_VENV/pyvenv.cfg" ]] && ln -s "$REAL_VENV/pyvenv.cfg" "$fake/.venv/pyvenv.cfg"
    local f base
    for f in "$REAL_VENV"/bin/*; do
        base="$(basename "$f")"
        case "$base" in
            uvicorn|uvicorn3) continue ;;  # this harness supplies its own
            # CRITICAL: never symlink any activate* variant here — the
            # `cat >` below would then follow the symlink and OVERWRITE
            # THE REAL VENV'S OWN activate script in place (this is not
            # hypothetical: it happened once while writing this file and
            # corrupted the real .venv/bin/activate; regenerated via
            # `python3.11 -m venv .venv`, no --clear, which rewrites only
            # the activation scripts and leaves site-packages untouched).
            activate|activate.*|Activate.*) continue ;;
        esac
        ln -s "$f" "$fake/.venv/bin/$base"
    done
    # A real, non-symlinked file — safe to `cat >` (see the CRITICAL note
    # in the loop above for why this must never be a pre-existing symlink).
    cat > "$fake/.venv/bin/activate" <<ACTIVATE
#!/usr/bin/env bash
# Minimal test-stub activate (tests/cli/lib.sh:make_fake_venv) — see that
# function's header for why the real venv's own activate can't be reused.
export VIRTUAL_ENV="${fake}/.venv"
export PATH="\${VIRTUAL_ENV}/bin:\$PATH"
ACTIVATE
    chmod +x "$fake/.venv/bin/activate"
}

fake_venv_python() {
    printf '%s/.venv/bin/python3' "$1"
}

# link_real_uvicorn <fake> — symlink the REAL, working uvicorn into a fake
# venv built by make_fake_venv (which otherwise never puts one there).
# For drivers that only need `command -v uvicorn` to succeed (e.g.
# `doctor`'s preflight) and never actually invoke it.
link_real_uvicorn() {
    local fake="$1"
    [[ -n "$REAL_VENV" && -e "$REAL_VENV/bin/uvicorn" ]] || return 1
    ln -sf "$REAL_VENV/bin/uvicorn" "$fake/.venv/bin/uvicorn"
}

# cli_test_spawn_stub_portal <port> <fixture-json-path> [http-status] —
# runs tests/cli/stub_uvicorn_serving.py DIRECTLY (no uvicorn shim, no
# start.sh) bound to 127.0.0.1:<port>, answering /api/instance with
# <fixture-json-path>'s body at the given HTTP status (default 200). For
# scenarios (status_driver.sh) that only need SOMETHING real answering the
# root portal port — never a start.sh boot. Sets
# CLI_TEST_LAST_FABRICATED_PID (same global-handoff convention as
# cli_test_fabricate_live_instance — NOT `pid=$(...)`, for the identical
# subshell-swallows-the-background-job reason documented there).
cli_test_spawn_stub_portal() {
    local port="$1" fixture="$2" http_status="${3:-200}"
    [[ -n "$REAL_VENV" ]] || return 1
    STUB_FIXTURE="$fixture" STUB_STATUS="$http_status" \
        "$REAL_VENV/bin/python" "$CLI_TEST_DIR/stub_uvicorn_serving.py" "arail.portal.app:app" "127.0.0.1" "$port" &
    CLI_TEST_LAST_FABRICATED_PID=$!
}

# ── The "serving" stub uvicorn (§16.1's enabling capability): actually
# BINDS <host>:<port> and answers /api/instance and /health for real,
# dialed per-scenario via env vars (see tests/cli/stub_uvicorn_serving.py's
# own header for the exact controls: STUB_STATUS, STUB_FIXTURE,
# STUB_CRASH_AFTER, STUB_NEVER_BIND).
write_stub_uvicorn_serving() {
    local fake="$1"
    cp "$CLI_TEST_DIR/stub_uvicorn_serving.py" "$fake/.venv/stub_uvicorn_serving.py"
    cat > "$fake/.venv/bin/uvicorn" <<EOF
#!/usr/bin/env bash
# A REAL-BINDING stub uvicorn (ARCHITECTURE.md §16.1) — parses just enough
# argv (module target + --host/--port) to hand off to the python stub
# server; every behavior knob is an env var the driver sets before it
# invokes start.sh/arailctl (see stub_uvicorn_serving.py).
#
# REVIEW.md B2/T35: deliberately NOT \`exec\`ing into python3 (the original
# shape). exec would REPLACE this wrapper's own argv with python3's — which
# drops --app-dir/--port from what pgrep -f / ps -o command= can see,
# since the python stub only receives the 3 positional args it needs. A
# golden-path scenario that drives a REAL \`stop --root\` (reset.sh's
# stop_services(), which finds its kill candidates via pgrep -f against
# the ORIGINAL uvicorn-shaped argv, exactly as production's real uvicorn
# binary is invoked) needs this wrapper's OWN argv — "uvicorn <module>
# --host H --app-dir D --port P --log-level L", byte-for-byte what
# start.sh actually passed — to stay visible the whole time it runs.
# Backgrounding the python child and forwarding TERM/INT to it (instead of
# exec) preserves that, with no change to the externally-observable
# behavior any existing scenario (T13-T17, T18a, warmup) depends on: the
# recorded pid still answers HTTP the instant it's up and still frees the
# port the instant it's killed.
module=""; host="127.0.0.1"; port="8080"
while [[ \$# -gt 0 ]]; do
    case "\$1" in
        --host) host="\$2"; shift 2 ;;
        --port) port="\$2"; shift 2 ;;
        --app-dir) shift 2 ;;
        --log-level) shift 2 ;;
        *) [[ -z "\$module" ]] && module="\$1"; shift ;;
    esac
done
"$fake/.venv/bin/python3" "$fake/.venv/stub_uvicorn_serving.py" "\$module" "\$host" "\$port" &
_stub_child=\$!
trap 'kill "\$_stub_child" 2>/dev/null; wait "\$_stub_child" 2>/dev/null; exit 0' TERM INT
wait "\$_stub_child"
EOF
    chmod +x "$fake/.venv/bin/uvicorn"
}

# ── Listen-only stubs (ttyd / jupyter / code-server): either bind <port>
# and hold it open, or (never_bind=1) start but never bind at all —
# simulating "installed, but the tab never comes up" (T15).
write_stub_listen_only() {
    local fake="$1" name="$2" port="$3" never_bind="${4:-0}"
    if [[ "$never_bind" == "1" ]]; then
        cat > "$fake/.venv/bin/$name" <<'EOF'
#!/usr/bin/env bash
trap 'exit 0' TERM INT
sleep 300 &
wait
EOF
    else
        cat > "$fake/.venv/bin/$name" <<EOF
#!/usr/bin/env bash
exec "$fake/.venv/bin/python3" -c "
import socket, signal, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', $port))
s.listen(5)
signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
signal.signal(signal.SIGINT, lambda *a: sys.exit(0))
signal.pause()
"
EOF
    fi
    chmod +x "$fake/.venv/bin/$name"
}

# cli_test_kill_port_listener <port> — best-effort cleanup for a scenario
# that simulates launchd/a supervisor spawning a REAL process this harness
# does not otherwise track in a PIDS array (e.g. a stub `launchctl
# kickstart` backgrounding a serving stub — nothing in that path is a
# child this driver's own `_timeout` process-group kill would reach, since
# the real production equivalent is "launchd owns it", which this harness
# only simulates). Never fails a driver on its own.
cli_test_kill_port_listener() {
    local port="$1" pid
    pid="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -n1)"
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null
    return 0
}

# ── lab.conf writer: F26/F27-safe random ports, all distinct.
write_lab_conf() {
    local fake="$1" portal="$2" lance="$3" terminal="$4" notebook="$5" ide="$6"
    cat > "$fake/lab.conf" <<EOF
PORTAL_PORT=${portal}
LANCE_PORT=${lance}
TERMINAL_PORT=${terminal}
NOTEBOOK_PORT=${notebook}
IDE_PORT=${ide}
BIND_ADDR=127.0.0.1
EOF
}

# ── install.sh fixtures (sprints/2026-07-29-elite-cli/ARCHITECTURE.md §6,
# WP7) — a REAL git repo tracking a REAL local bare "remote" (§16.1: "phases
# against a local bare git 'remote'"), never a simulation of git plumbing.
#
# cli_test_make_git_install_repo <fake-dir> <bare-remote-dir> [<branch>] —
# builds <fake-dir> via make_fake_repo (the real scripts/ + arailctl), adds
# a .gitignore covering everything a scenario's OWN fixture setup touches
# afterward (.venv/, lab/, .env, model_defaults.yaml, lab.conf) so those
# never make the worktree look dirty on their own, commits everything else,
# and pushes to a freshly-created bare remote with upstream tracking
# already configured — install.sh's source-phase preconditions (clean,
# attached HEAD, tracking branch) all pass by construction. A scenario that
# needs to test the DIRTY-worktree refusal (T25) does so by touching a
# TRACKED file afterward.
cli_test_make_git_install_repo() {
    local fake="$1" bare="$2" branch="${3:-main}"
    git init --quiet --bare -b "$branch" "$bare" >/dev/null
    make_fake_repo "$fake" >/dev/null
    cat > "$fake/.gitignore" <<'EOF'
.venv/
lab/
.env
model_defaults.yaml
lab.conf
EOF
    git -C "$fake" init --quiet -b "$branch" >/dev/null
    git -C "$fake" config user.email "cli-test@example.com"
    git -C "$fake" config user.name "cli-test"
    git -C "$fake" add -A
    git -C "$fake" commit --quiet -m "initial" >/dev/null
    git -C "$fake" remote add origin "$bare"
    git -C "$fake" push --quiet -u origin "$branch" >/dev/null
}

# cli_test_publish_git_change <bare-remote-dir> <branch> <relpath> <content>
# — commits <content> to <relpath> DIRECTLY INTO THE BARE REMOTE via a
# throwaway clone (never touches the fake repo's own worktree — this is
# the "an operator pushed a new commit upstream" half of T24/F5, kept
# separate from the fake repo under test so a re-exec's "new code" is
# genuinely different bytes on disk, not a same-shell illusion). Prints
# the new commit's sha on stdout.
cli_test_publish_git_change() {
    local bare="$1" branch="$2" relpath="$3" content="$4"
    local tmp
    tmp="$(mktemp -d)"
    git clone --quiet "$bare" "$tmp" >/dev/null 2>&1
    git -C "$tmp" checkout --quiet "$branch" >/dev/null 2>&1 || true
    mkdir -p "$(dirname "$tmp/$relpath")"
    printf '%s' "$content" > "$tmp/$relpath"
    git -C "$tmp" add -A
    git -C "$tmp" -c user.email=cli-test@example.com -c user.name=cli-test commit --quiet -m "update $relpath" >/dev/null
    git -C "$tmp" push --quiet origin "$branch" >/dev/null
    git -C "$tmp" rev-parse HEAD
    rm -rf "$tmp"
}

# cli_test_mark_provisioned <fake-dir> [<lab_mode>] — the minimum an
# install.sh scenario needs to pass the "provisioned?" preflight check
# (.venv dir + .env file present). Uses the REAL working venv (via
# make_fake_venv, so `arailctl doctor`'s own `source .venv/bin/activate`
# succeeds and `import arail` works) when REAL_VENV is available — always
# true here (install_driver.sh self-skips otherwise) — falling back to a
# bare directory only for scenarios that never reach a phase needing it.
cli_test_mark_provisioned() {
    local fake="$1" lab_mode="${2:-hybrid}"
    if [[ -n "$REAL_VENV" ]]; then
        make_fake_venv "$fake"
    else
        mkdir -p "$fake/.venv/bin"
    fi
    printf 'LAB_MODE=%s\nLAB_TIER=minimalist\n' "$lab_mode" > "$fake/.env"
}
